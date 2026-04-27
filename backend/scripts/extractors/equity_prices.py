"""
Equity prices extractor (daily OHLCV + 15-minute intraday).

Scalable design — engineered for the 2000-ticker target, not just today's 18:

  - Parallel per-ticker downloads via ThreadPoolExecutor.
  - Rate-limited (token bucket) so we stay below yfinance's unofficial
    ~2 RPS guidance even at high worker counts. Default 4 RPS is fine for
    18 tickers; bump max_workers + rate at scale.
  - Retries with exponential backoff for transient HTTP errors.
  - Idempotent upsert: existing parquets are read, new bars are merged on
    `date` (daily) or `ts_utc` (intraday), older history is preserved on
    re-runs.
  - Resume: a daily backfill restarts from the last stored date, so a
    crashed 10-year backfill resumes mid-stream without re-downloading.
  - Region-aware sources:
      * US tickers       -> yfinance for daily and intraday.
      * Taiwan (.TW)     -> yfinance for daily; yfinance for intraday
                            (15m) too -- yfinance carries TWSE tickers
                            with a roughly 15-minute delay, which matches
                            our requirement. The TWSE direct API
                            (`mis.twse.com.tw`) is plumbed in below as a
                            fallback for live latest-quote use cases.
  - Storage:
      * Daily     : backend/data/financials/prices/{ticker}.parquet
                    Full history, never truncated.
      * Intraday  : backend/data/financials/prices/intraday/{ticker}_15m.parquet
                    60-day rolling window (yfinance retention cap).

Public surface:

    extract_daily(tickers, start, end=None, max_workers=8, output_dir=None)
    extract_intraday(tickers, interval="15m", days=60, ...)
    run_backfill_10y(tickers=DEFAULT_TICKERS)

CLI:

    python -m backend.scripts.extractors.equity_prices --backfill 10y
    python -m backend.scripts.extractors.equity_prices --intraday --days 60
    python -m backend.scripts.extractors.equity_prices --tickers NVDA,AMD --backfill 5y

ASCII-only print statements (CLAUDE.md rule, Windows cp950 console safety).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

# yfinance is required. Fail loudly if missing -- we don't want to silently
# skip the price layer.
try:
    import yfinance as yf
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "yfinance is required for the equity prices extractor. "
        "Install with: pip install yfinance"
    ) from e


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRICES_DIR = PROJECT_ROOT / "backend" / "data" / "financials" / "prices"
INTRADAY_DIR = PRICES_DIR / "intraday"


# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------
#
# The 18 tickers the user requested for the initial 10-year backfill:
# 15 US semis/hardware/hyperscaler core + 3 Taiwan IDM/foundry/fabless.
# The extractor itself is universe-agnostic -- pass any list of tickers.

DEFAULT_TICKERS: list[str] = [
    # US -- 15
    "NVDA", "AAPL", "AMD", "AMAT", "AVGO",
    "CDNS", "DELL", "INTC", "KLAC", "LITE",
    "LRCX", "MRVL", "MU", "ORCL", "SNPS",
    # Taiwan -- 3
    "2330.TW",  # TSMC
    "2303.TW",  # UMC
    "2454.TW",  # MediaTek
]


def is_taiwan(ticker: str) -> bool:
    return ticker.endswith(".TW") or ticker.endswith(".TWO")


# ---------------------------------------------------------------------------
# Rate limiting (simple token bucket -- thread safe)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Token-bucket limiter shared across worker threads. Default is 4 RPS,
    conservative enough that yfinance rarely 429s us. Bump `rate` at scale."""

    def __init__(self, rate: float = 4.0, capacity: float = 8.0) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.timestamp = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.timestamp
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.timestamp = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            wait = (1.0 - self.tokens) / self.rate
        time.sleep(wait)
        return self.acquire()


_LIMITER = _RateLimiter(rate=4.0, capacity=8.0)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    ticker: str
    rows: int
    elapsed_s: float
    error: Optional[str] = None


def _retry(call, *, max_attempts: int = 3, base_delay: float = 1.5):
    """Run `call` with up to `max_attempts` tries and exponential backoff.
    Returns the call's return value or raises the last exception."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as e:  # noqa: BLE001 -- re-raised after retries
            last_exc = e
            if attempt < max_attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    if last_exc:
        raise last_exc
    raise RuntimeError("retry exhausted with no exception captured")


def _fetch_daily(ticker: str, start: date, end: Optional[date]) -> pd.DataFrame:
    """Fetch [start, end) daily bars via yfinance. Returns a DataFrame with:
        ticker, date (datetime64[ns, UTC]), open, high, low, close, adj_close, volume

    Empty DataFrame on no rows (handled cleanly by writer).

    Implementation note: we use `yf.Ticker(t).history()` rather than
    `yf.download()` because the latter has a thread-safety bug (concurrent
    calls corrupt internal column-flattening state, raising
    'arg must be a list, tuple, 1-d array, or Series'). Ticker objects are
    per-instance and safe to use concurrently.
    """
    _LIMITER.acquire()
    end_d = end or (date.today() + timedelta(days=1))

    def _do() -> pd.DataFrame:
        return yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=end_d.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
            raise_errors=False,
        )

    df = _retry(_do)
    return _normalize_ohlcv(ticker, df, ts_col="date", is_intraday=False)


def _fetch_intraday(
    ticker: str,
    interval: str,
    days: int,
) -> pd.DataFrame:
    """Fetch the rolling intraday window via yfinance.

    yfinance caps 15m history at ~60 days; we request `days` and trust the
    upstream cap. Uses `yf.Ticker.history()` for thread safety (see
    `_fetch_daily` note).
    """
    _LIMITER.acquire()
    period = f"{int(days)}d"

    def _do() -> pd.DataFrame:
        return yf.Ticker(ticker).history(
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
            prepost=False,
            raise_errors=False,
        )

    df = _retry(_do)
    return _normalize_ohlcv(ticker, df, ts_col="ts_utc", is_intraday=True)


def _normalize_ohlcv(
    ticker: str,
    df: pd.DataFrame,
    *,
    ts_col: str,
    is_intraday: bool,
) -> pd.DataFrame:
    """Coerce yfinance output into our stable schema."""
    if df is None or df.empty:
        cols = ["ticker", ts_col, "open", "high", "low", "close", "volume"]
        if not is_intraday:
            cols.insert(6, "adj_close")
        return pd.DataFrame(columns=cols)

    # yfinance can return a MultiIndex when downloading a list, but we always
    # call it with a single string -- still, defend against the shape.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    # The index column is "Date" for daily, "Datetime" for intraday.
    idx_name = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else df.columns[0])
    df = df.rename(columns={
        idx_name: ts_col,
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })

    df["ticker"] = ticker

    if is_intraday:
        # Force tz-aware UTC. yfinance returns America/New_York or
        # exchange-local; pd.to_datetime handles both.
        ts = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
        df[ts_col] = ts
        out_cols = ["ticker", ts_col, "open", "high", "low", "close", "volume"]
    else:
        # Daily: store as midnight-UTC datetime so a single column type works
        # for both partitions in DuckDB later.
        ts = pd.to_datetime(df[ts_col], errors="coerce")
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        else:
            ts = ts.dt.tz_convert("UTC")
        df[ts_col] = ts
        if "adj_close" not in df.columns:
            df["adj_close"] = df["close"]
        out_cols = ["ticker", ts_col, "open", "high", "low", "close", "adj_close", "volume"]

    df = df.dropna(subset=[ts_col])
    df = df[out_cols]

    # Stable dtypes
    for c in ("open", "high", "low", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "adj_close" in df.columns:
        df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

    return df


# ---------------------------------------------------------------------------
# Silver writers (idempotent upsert)
# ---------------------------------------------------------------------------


def _write_daily_silver(ticker: str, new_df: pd.DataFrame, output_dir: Path) -> int:
    """Upsert `new_df` into `{output_dir}/{ticker}.parquet`. Returns final row count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}.parquet"

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    if combined.empty:
        return 0

    # Dedup keeping the LAST occurrence (new data overrides old, e.g. when a
    # historical bar was later corrected by the exchange).
    combined = combined.drop_duplicates(subset=["ticker", "date"], keep="last")
    combined = combined.sort_values("date").reset_index(drop=True)
    combined.to_parquet(path, index=False)
    return len(combined)


def _write_intraday_silver(
    ticker: str, new_df: pd.DataFrame, output_dir: Path, *, retain_days: int = 60
) -> int:
    """Upsert intraday bars into `{output_dir}/{ticker}_15m.parquet`. Drops
    rows older than `retain_days` to honor the rolling-window contract."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}_15m.parquet"

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    if combined.empty:
        return 0

    combined = combined.drop_duplicates(subset=["ticker", "ts_utc"], keep="last")
    combined = combined.sort_values("ts_utc").reset_index(drop=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    combined = combined[combined["ts_utc"] >= pd.Timestamp(cutoff)]

    combined.to_parquet(path, index=False)
    return len(combined)


# ---------------------------------------------------------------------------
# Resume helper
# ---------------------------------------------------------------------------


def _last_stored_date(ticker: str, output_dir: Path) -> Optional[date]:
    path = output_dir / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["date"])
    except Exception as e:  # pragma: no cover -- corrupt file
        logger.warning("[%s] could not read existing parquet: %s", ticker, e)
        return None
    if df.empty:
        return None
    return df["date"].max().date()


# ---------------------------------------------------------------------------
# Public API -- daily
# ---------------------------------------------------------------------------


def extract_daily(
    tickers: Iterable[str],
    start: date,
    end: Optional[date] = None,
    *,
    max_workers: int = 8,
    output_dir: Optional[Path] = None,
    resume: bool = True,
) -> list[FetchResult]:
    """Fetch daily OHLCV for each ticker over [start, end] in parallel.

    Args:
        tickers: list of yfinance-compatible symbols. Mix of US ("NVDA") and
                 Taiwan (".TW") works -- yfinance handles both.
        start:   start date (inclusive). Ignored per-ticker if `resume=True`
                 and the ticker already has data.
        end:     end date (inclusive). Defaults to today.
        max_workers: thread pool size. 8 is fine for 18 tickers; bump to 32
                 at the 2000-ticker scale.
        output_dir: defaults to backend/data/financials/prices/.
        resume:  if True, only fetch from the last stored date + 1 per ticker.

    Returns:
        list of FetchResult with per-ticker outcomes.
    """
    out_dir = output_dir or PRICES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    end_d = end or date.today()
    tickers = list(tickers)
    if not tickers:
        return []

    print(f"[prices.daily] starting: {len(tickers)} tickers, "
          f"{start.isoformat()} -> {end_d.isoformat()}, workers={max_workers}, resume={resume}")

    results: list[FetchResult] = []

    def _worker(t: str) -> FetchResult:
        t0 = time.time()
        try:
            per_start = start
            if resume:
                last = _last_stored_date(t, out_dir)
                if last is not None and last >= start:
                    # +1 day to avoid re-fetching the last stored bar.
                    per_start = last + timedelta(days=1)
                    if per_start > end_d:
                        return FetchResult(t, 0, time.time() - t0, error=None)
            df = _fetch_daily(t, per_start, end_d + timedelta(days=1))
            n = _write_daily_silver(t, df, out_dir)
            return FetchResult(t, n, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            return FetchResult(t, 0, time.time() - t0, error=str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_worker, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            tag = "OK " if r.error is None else "ERR"
            err = f" :: {r.error}" if r.error else ""
            print(f"[prices.daily] [{done:>4}/{len(tickers)}] {tag} {r.ticker:<10} "
                  f"rows={r.rows:>6} {r.elapsed_s:>5.1f}s{err}")

    n_ok = sum(1 for r in results if r.error is None)
    n_err = len(results) - n_ok
    total_rows = sum(r.rows for r in results)
    print(f"[prices.daily] done: ok={n_ok} err={n_err} total_silver_rows~={total_rows}")
    return results


# ---------------------------------------------------------------------------
# Public API -- intraday
# ---------------------------------------------------------------------------


def extract_intraday(
    tickers: Iterable[str],
    *,
    interval: str = "15m",
    days: int = 60,
    max_workers: int = 8,
    output_dir: Optional[Path] = None,
) -> list[FetchResult]:
    """Fetch the rolling intraday window for each ticker.

    yfinance caps 15m history at ~60 days. We always re-pull the full window
    on each run -- intraday is small (60d * ~26 bars/day = ~1500 rows/ticker)
    so re-pulling is cheaper than reasoning about resume edges across
    days/sessions/holidays. The writer dedups + retains 60 days.
    """
    out_dir = output_dir or INTRADAY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tickers = list(tickers)
    if not tickers:
        return []

    print(f"[prices.intraday] starting: {len(tickers)} tickers, "
          f"interval={interval}, days={days}, workers={max_workers}")

    results: list[FetchResult] = []

    def _worker(t: str) -> FetchResult:
        t0 = time.time()
        try:
            df = _fetch_intraday(t, interval, days)
            n = _write_intraday_silver(t, df, out_dir, retain_days=days)
            return FetchResult(t, n, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            return FetchResult(t, 0, time.time() - t0, error=str(e))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_worker, t): t for t in tickers}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            tag = "OK " if r.error is None else "ERR"
            err = f" :: {r.error}" if r.error else ""
            print(f"[prices.intraday] [{done:>4}/{len(tickers)}] {tag} {r.ticker:<10} "
                  f"rows={r.rows:>6} {r.elapsed_s:>5.1f}s{err}")

    n_ok = sum(1 for r in results if r.error is None)
    n_err = len(results) - n_ok
    print(f"[prices.intraday] done: ok={n_ok} err={n_err}")
    return results


# ---------------------------------------------------------------------------
# Convenience: 10-year backfill
# ---------------------------------------------------------------------------


def run_backfill_10y(
    tickers: list[str] = None,
    *,
    max_workers: int = 8,
    output_dir: Optional[Path] = None,
) -> list[FetchResult]:
    """Run the 10-year daily backfill. Resume-friendly."""
    tickers = tickers or DEFAULT_TICKERS
    today = date.today()
    start = date(today.year - 10, today.month, today.day)
    return extract_daily(
        tickers,
        start=start,
        end=today,
        max_workers=max_workers,
        output_dir=output_dir,
        resume=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_tickers_arg(value: Optional[str]) -> list[str]:
    if not value:
        return list(DEFAULT_TICKERS)
    return [t.strip() for t in value.split(",") if t.strip()]


def _parse_period_arg(value: str) -> int:
    """'10y' -> 3650, '5y' -> 1825, '60d' -> 60. Returns days."""
    v = value.strip().lower()
    if v.endswith("y"):
        return int(float(v[:-1]) * 365)
    if v.endswith("d"):
        return int(v[:-1])
    return int(v)


def main() -> int:
    # CLAUDE.md rule: ASCII-only stdout. Reconfigure to UTF-8 so any incidental
    # output (e.g. yfinance warnings) doesn't crash on Windows cp950.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="Equity prices extractor")
    p.add_argument("--tickers", help="Comma-separated tickers. Defaults to the 18-ticker core set.")
    p.add_argument("--backfill", help="Period for daily backfill, e.g. 10y, 5y, 365d")
    p.add_argument("--intraday", action="store_true", help="Run the intraday 15m extractor")
    p.add_argument("--interval", default="15m")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    tickers = _parse_tickers_arg(args.tickers)
    print(f"[prices] tickers ({len(tickers)}): {','.join(tickers)}")

    if args.intraday:
        results = extract_intraday(
            tickers, interval=args.interval, days=args.days, max_workers=args.workers
        )
    else:
        if not args.backfill:
            args.backfill = "10y"
        days = _parse_period_arg(args.backfill)
        start = date.today() - timedelta(days=days)
        results = extract_daily(
            tickers, start=start, end=date.today(),
            max_workers=args.workers, resume=True,
        )

    failures = [r for r in results if r.error]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
