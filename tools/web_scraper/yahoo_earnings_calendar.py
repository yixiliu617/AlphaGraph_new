"""
Yahoo Finance earnings-calendar verifier.

Cross-references NASDAQ's primary earnings calendar against Yahoo's per-ticker
next-earnings-date. We don't use Yahoo as the primary source -- it returns
only the SINGLE next earnings date per ticker, not a calendar of all
upcoming names. Instead we hit it once per ticker in our universe and ask:
"Yahoo, does ticker X have an upcoming earnings date, and if so, when?"

Result is used by `backend/scripts/refresh_calendar_us.py` to set the
`verification` field on each NASDAQ-sourced calendar event:
  - "nasdaq+yahoo_match"  : Yahoo's date matches NASDAQ's within 1 day
  - "nasdaq_only"         : Yahoo has no upcoming date (or yfinance failed)
  - "date_disagreement"   : Yahoo has a date but it's > 1 day from NASDAQ

Cache-first rule (CLAUDE.md): every yfinance result is persisted under
    backend/data/_raw/yahoo_earnings_calendar/<TICKER>_<YYYY-MM-DD>.json
keyed by snapshot date so we keep daily history for audit.

Usage (programmatic):
    from tools.web_scraper.yahoo_earnings_calendar import fetch_next_dates
    next_dates = fetch_next_dates(["NVDA", "KLAC", "AAPL"])
    # -> {"NVDA": datetime.date(2026, 5, 21), "KLAC": ..., "AAPL": ...}

CLI (manual):
    python tools/web_scraper/yahoo_earnings_calendar.py fetch --ticker NVDA
    python tools/web_scraper/yahoo_earnings_calendar.py fetch --tickers NVDA KLAC AAPL

ASCII-only print/log.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _PROJECT_ROOT / "backend" / "data" / "_raw" / "yahoo_earnings_calendar"


# ---------------------------------------------------------------------------
# Single-ticker fetch
# ---------------------------------------------------------------------------

def _serialize_calendar(cal: dict) -> dict:
    """Convert yfinance Ticker.calendar dict (which contains date and float
    objects) to a JSON-friendly dict for bronze persistence."""
    out: dict = {}
    for k, v in cal.items():
        if isinstance(v, list):
            out[k] = [_serialize_value(x) for x in v]
        else:
            out[k] = _serialize_value(v)
    return out


def _serialize_value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def fetch_one_ticker(ticker: str, *, snapshot_date: date | None = None) -> date | None:
    """Return the next earnings date Yahoo reports for `ticker`, or None.

    Persists raw yfinance.calendar dict to bronze. The bronze key is
    snapshot-dated so we keep daily history of what Yahoo said when (useful
    for forensics if a date moves around in the run-up to earnings)."""
    snapshot_date = snapshot_date or datetime.utcnow().date()
    snapshot_key = snapshot_date.strftime("%Y-%m-%d")

    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed -- pip install yfinance")
        return None

    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
    except Exception as exc:
        logger.warning("[%s] yfinance.calendar failed: %s", ticker, exc)
        return None

    if not cal or not isinstance(cal, dict):
        return None

    # Bronze persist regardless of whether we found a date.
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RAW_DIR / f"{ticker}_{snapshot_key}.json"
    try:
        out_path.write_text(json.dumps(_serialize_calendar(cal), indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("[%s] failed to persist yahoo bronze: %s", ticker, exc)

    earnings_dates = cal.get("Earnings Date") or []
    if not earnings_dates:
        return None

    # yfinance returns a list of dates -- normally just one. Pick the
    # earliest that's >= today (sometimes Yahoo returns past dates).
    today = datetime.utcnow().date()
    future = [d for d in earnings_dates if isinstance(d, date) and d >= today]
    if future:
        return min(future)
    # All dates in the past -- fall back to the latest one (Yahoo lag).
    valid = [d for d in earnings_dates if isinstance(d, date)]
    return max(valid) if valid else None


# ---------------------------------------------------------------------------
# Bulk fetch
# ---------------------------------------------------------------------------

def fetch_next_dates(
    tickers: Iterable[str],
    *,
    sleep_between: float = 0.4,
    snapshot_date: date | None = None,
) -> dict[str, date | None]:
    """Fetch next earnings date for many tickers. Sleeps between calls to
    avoid spamming Yahoo. Returns {ticker: next_date_or_None}."""
    out: dict[str, date | None] = {}
    for i, t in enumerate(tickers):
        nxt = fetch_one_ticker(t, snapshot_date=snapshot_date)
        out[t] = nxt
        logger.info("[%s] next earnings date: %s", t, nxt or "<none>")
        if i < len(list(tickers)) - 1:
            time.sleep(sleep_between)
    return out


# ---------------------------------------------------------------------------
# Cross-reference helper
# ---------------------------------------------------------------------------

def cross_reference(
    nasdaq_events: list[dict],
    yahoo_dates: dict[str, date | None],
    *,
    tolerance_days: int = 1,
) -> list[dict]:
    """Annotate NASDAQ events with a `verification` field by comparing
    against Yahoo's next-earnings-date map.

    Mutates each event dict in-place AND returns the list (convenience).

    Verification values:
        "nasdaq+yahoo_match" : abs(nasdaq_date - yahoo_date) <= tolerance_days
        "date_disagreement"  : both have a date but they differ > tolerance
        "nasdaq_only"        : Yahoo has no date for this ticker, or the
                                date is outside our calendar window
    """
    for ev in nasdaq_events:
        ticker = ev.get("ticker", "")
        nasdaq_dt = ev.get("release_datetime_utc")
        if not isinstance(nasdaq_dt, datetime):
            ev["verification"] = "nasdaq_only"
            continue
        nasdaq_date = nasdaq_dt.date()

        yahoo_date = yahoo_dates.get(ticker)
        if yahoo_date is None:
            ev["verification"] = "nasdaq_only"
            continue
        delta = abs((nasdaq_date - yahoo_date).days)
        if delta <= tolerance_days:
            ev["verification"] = "nasdaq+yahoo_match"
        else:
            ev["verification"] = "date_disagreement"
    return nasdaq_events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    p = argparse.ArgumentParser(description="Yahoo Finance earnings-date verifier")
    sub = p.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch", help="Fetch Yahoo next earnings date(s)")
    fp.add_argument("--ticker", help="Single ticker")
    fp.add_argument("--tickers", nargs="+", help="Multiple tickers")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.cmd == "fetch":
        targets = []
        if args.ticker:
            targets.append(args.ticker.upper())
        if args.tickers:
            targets.extend(t.upper() for t in args.tickers)
        if not targets:
            print("specify --ticker or --tickers")
            return 1
        result = fetch_next_dates(targets)
        for tkr, dt in result.items():
            print(f"  {tkr:<8} {dt or '<none>'}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
