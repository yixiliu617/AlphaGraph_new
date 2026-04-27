"""
TWSE direct daily prices extractor.

Hits TWSE's STOCK_DAY OpenData endpoint to fetch RAW OHLCV — used as a
patching layer over yfinance to (a) heal Yahoo's historical gaps in
TWSE data (~5% of pre-2020 sessions missing per ticker) and (b) provide
authoritative same-day close after the 13:30 TPE close, before Yahoo
publishes.

Why yfinance + TWSE rather than TWSE alone:
  - yfinance gives us split-adjusted history for free across both US +
    TW with one code path. Useful default for cross-region charts.
  - TWSE STOCK_DAY publishes RAW OHLCV (no split-adjustment). To merge
    with the existing yfinance silver, we derive a multiplicative
    adjustment factor from any overlapping (date) bars where both
    sources have data and the prices agree to within tolerance — that
    factor is then applied to the TWSE bars we're about to insert into
    the silver parquet.
  - For the major TW tickers we track (2330 / 2303 / 2454) the factor
    is essentially 1.0 — they don't split frequently, and Yahoo's
    recent split-adjusted bars match TWSE raw bars to ~3 decimals.
  - If the factor diverges significantly (>0.5%) across the overlap,
    we LOG WARN and abort the patch for that ticker rather than
    silently writing wrong data.

Endpoint:
    https://www.twse.com.tw/exchangeReport/STOCK_DAY
        ?response=json&date=YYYYMMDD&stockNo=NNNN

Returns OHLCV for the entire month containing `date`. Date strings are
in ROC calendar (民國紀年): "114/01/02" -> 2025-01-02 (year + 1911).
Volume is in shares. Price columns: 開盤價 / 最高價 / 最低價 / 收盤價.

Used by:
  - The nightly `prices.taiwan_twse_patch` job (overwrites prior 30
    days of .TW silver with TWSE-merged data).
  - The CLI for ad-hoc backfill / sanity-check.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests
import urllib3


# Disable urllib3's InsecureRequestWarning. We deliberately use verify=False
# below for TWSE government hosts whose certs fail strict Python 3.13 OpenSSL
# validation (Missing Subject Key Identifier). Same pattern as the existing
# taiwan/scrapers/twse_historical.py and tpex_openapi.py modules. The data is
# public and the host is the government's own domain, so MITM risk is the
# only concern -- mitigated by checking response shape.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


logger = logging.getLogger(__name__)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
PRICES_DIR = _PROJECT_ROOT / "backend" / "data" / "financials" / "prices"

_TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.8,zh-TW;q=0.6",
    "Referer": "https://www.twse.com.tw/",
}

# Politeness pause between TWSE requests; TWSE rate-limits aggressive callers.
# 0.6s for ad-hoc / nightly use. Historical runs use a longer pause via
# `_TWSE_HIST_INTERVAL_S` to reduce 429-style "Expecting value" responses.
_TWSE_REQUEST_INTERVAL_S = 0.6
_TWSE_HIST_INTERVAL_S = 1.5


# ---------------------------------------------------------------------------
# ROC date helpers
# ---------------------------------------------------------------------------

def roc_to_iso(roc_date: str) -> Optional[str]:
    """'114/01/02' -> '2025-01-02'. Returns None on bad input."""
    try:
        parts = roc_date.strip().split("/")
        if len(parts) != 3:
            return None
        y = int(parts[0]) + 1911
        m = int(parts[1])
        d = int(parts[2])
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (ValueError, IndexError):
        return None


def _parse_num(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.strip().replace(",", "")
    if not s or s in {"--", "-", "X"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# TWSE STOCK_DAY fetcher
# ---------------------------------------------------------------------------

def fetch_twse_month(
    ticker_no: str,
    year: int,
    month: int,
    *,
    timeout: int = 30,
    retries: int = 3,
) -> pd.DataFrame:
    """Fetch one month of TWSE daily OHLCV for `ticker_no` (e.g. '2330').

    Returns DataFrame with columns:
        date (str, ISO), open, high, low, close, volume (int shares)

    Empty DataFrame if TWSE returns no data (stat != 'OK', or no rows).
    """
    date_str = f"{year:04d}{month:02d}01"
    url = (
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?response=json&date={date_str}&stockNo={ticker_no}"
    )

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_TWSE_HEADERS, timeout=timeout, verify=False)
            r.raise_for_status()
            j = r.json()
            break
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries:
                time.sleep(1.5 * attempt)
            else:
                logger.warning("TWSE fetch %s %04d-%02d failed after %d tries: %s",
                               ticker_no, year, month, retries, e)
                return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    if last_exc is not None and "j" not in dir():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    if j.get("stat") != "OK":
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    rows = []
    for raw in j.get("data", []):
        if not raw or len(raw) < 7:
            continue
        iso = roc_to_iso(str(raw[0]))
        if not iso:
            continue
        vol_raw = (raw[1] or "").replace(",", "")
        try:
            vol = int(vol_raw) if vol_raw and vol_raw != "--" else 0
        except ValueError:
            vol = 0
        o = _parse_num(str(raw[3])) if len(raw) > 3 else None
        h = _parse_num(str(raw[4])) if len(raw) > 4 else None
        lo = _parse_num(str(raw[5])) if len(raw) > 5 else None
        c = _parse_num(str(raw[6])) if len(raw) > 6 else None
        if c is None:
            continue
        rows.append({
            "date": iso, "open": o, "high": h, "low": lo, "close": c, "volume": vol,
        })

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return df


def fetch_twse_recent(ticker_no: str, days: int = 30) -> pd.DataFrame:
    """Fetch ~`days` of recent TWSE OHLCV. Spans month boundaries by
    fetching the current month + prior month(s) as needed."""
    today = date.today()
    months_back = max(1, (days + 30) // 28)  # 30d -> 2 months; safer than 1
    months: list[tuple[int, int]] = []
    y, m = today.year, today.month
    for _ in range(months_back + 1):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    pieces: list[pd.DataFrame] = []
    for (yi, mi) in sorted(set(months)):
        df = fetch_twse_month(ticker_no, yi, mi)
        if not df.empty:
            pieces.append(df)
        time.sleep(_TWSE_REQUEST_INTERVAL_S)
    if not pieces:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    out = pd.concat(pieces, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    cutoff = pd.Timestamp(today - timedelta(days=days))
    out = out[out["date"] >= cutoff].sort_values("date").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Sanity check: yfinance vs TWSE
# ---------------------------------------------------------------------------

def sanity_check(ticker_tw: str = "2330.TW", days: int = 30) -> dict:
    """Compare yfinance silver (already on disk) vs TWSE direct for the
    last `days` of overlap. Returns:

        {
          'ticker': '2330.TW',
          'overlap_dates': N,
          'twse_only_dates': [...],     # dates Yahoo missed
          'yahoo_only_dates': [...],
          'close_ratio_median': float,  # ~1.0 if no recent split
          'close_ratio_min': float,
          'close_ratio_max': float,
          'max_close_diff_pct': float,  # max |yahoo-twse|/twse * 100
        }

    Date alignment: yfinance returns TWSE bars with timestamps at TPE
    midnight (16:00 UTC of the prior calendar day). We convert to TPE
    timezone and take the local date so the comparison matches TWSE's
    own date convention.
    """
    ticker_no = ticker_tw.replace(".TW", "").replace(".TWO", "")
    twse = fetch_twse_recent(ticker_no, days=days)
    silver_path = PRICES_DIR / f"{ticker_tw}.parquet"
    if not silver_path.exists():
        return {"ticker": ticker_tw, "error": f"silver missing at {silver_path}"}
    yf = pd.read_parquet(silver_path)
    yf["date"] = pd.to_datetime(yf["date"]).dt.tz_convert("Asia/Taipei").dt.normalize().dt.tz_localize(None)
    twse["date"] = pd.to_datetime(twse["date"]).dt.normalize()

    cutoff = pd.Timestamp(date.today() - timedelta(days=days))
    yf = yf[yf["date"] >= cutoff].copy()

    merged = yf[["date", "close"]].merge(
        twse[["date", "close"]],
        on="date", how="outer", suffixes=("_yf", "_twse"),
    ).sort_values("date").reset_index(drop=True)

    overlap = merged.dropna(subset=["close_yf", "close_twse"])
    twse_only = merged[merged["close_yf"].isna() & merged["close_twse"].notna()]
    yahoo_only = merged[merged["close_yf"].notna() & merged["close_twse"].isna()]

    ratios = (overlap["close_yf"] / overlap["close_twse"]).replace([float("inf")], pd.NA).dropna() if not overlap.empty else pd.Series([], dtype=float)
    diffs = ((overlap["close_yf"] - overlap["close_twse"]).abs() / overlap["close_twse"] * 100) if not overlap.empty else pd.Series([], dtype=float)

    return {
        "ticker": ticker_tw,
        "overlap_dates": int(len(overlap)),
        "twse_only_dates": twse_only["date"].dt.date.astype(str).tolist(),
        "yahoo_only_dates": yahoo_only["date"].dt.date.astype(str).tolist(),
        "close_ratio_median": float(ratios.median()) if not ratios.empty else None,
        "close_ratio_min": float(ratios.min()) if not ratios.empty else None,
        "close_ratio_max": float(ratios.max()) if not ratios.empty else None,
        "max_close_diff_pct": float(diffs.max()) if not diffs.empty else None,
    }


# ---------------------------------------------------------------------------
# Patch silver with TWSE-merged data
# ---------------------------------------------------------------------------

def _derive_adjustment_factor(
    overlap_yf: pd.DataFrame,
    overlap_twse: pd.DataFrame,
    *,
    tolerance_pct: float = 0.5,
) -> Optional[float]:
    """For overlapping dates, return the median ratio yf/twse if it's
    consistent across the overlap (within `tolerance_pct`). Returns None
    if the ratio varies more than tolerance — meaning a split or
    corp-action-driven discontinuity exists in the window and we can't
    safely apply a single factor."""
    if overlap_yf.empty or overlap_twse.empty:
        return None
    merged = overlap_yf[["date", "close"]].merge(
        overlap_twse[["date", "close"]],
        on="date", how="inner", suffixes=("_yf", "_twse"),
    )
    if merged.empty:
        return None
    ratios = merged["close_yf"] / merged["close_twse"]
    if ratios.isna().all():
        return None
    med = float(ratios.median())
    spread_pct = float(((ratios - med).abs() / med * 100).max())
    if spread_pct > tolerance_pct:
        logger.warning("Adjustment ratio spread %.2f%% exceeds %.2f%% — refusing to patch",
                       spread_pct, tolerance_pct)
        return None
    return med


def patch_silver_with_twse(
    ticker_tw: str,
    *,
    days: int = 30,
    overwrite_overlap: bool = True,
) -> dict:
    """Fetch last `days` of TWSE OHLCV for `ticker_tw` and merge into the
    existing silver parquet. Behaviour:

    1. Read the current silver.
    2. Fetch TWSE for the same date range.
    3. Find overlapping dates; derive the adjustment factor.
       - If the factor is None (varies too much), abort: data quality issue.
    4. Apply factor to TWSE bars.
    5. For dates Yahoo is missing, INSERT TWSE-adjusted bars (`source` field
       set to indicate provenance — but our current schema doesn't have a
       `source` column on prices yet, so we just write them in-place with
       a flag in `_source_meta` if the schema gains it).
    6. If `overwrite_overlap=True`, also overwrite Yahoo bars that exist
       on overlapping dates with the TWSE-adjusted values (assumes TWSE
       is authoritative for recent days). This heals same-day glitches in
       Yahoo's feed.

    Returns a stats dict.
    """
    out: dict = {"ticker": ticker_tw, "patched": 0, "filled_gap": 0,
                 "skipped_reason": None}
    silver_path = PRICES_DIR / f"{ticker_tw}.parquet"
    if not silver_path.exists():
        out["skipped_reason"] = "silver missing"
        return out

    yf = pd.read_parquet(silver_path)
    # Keep the original UTC `date` column for writing, but build a TPE-local
    # `tpe_date` column for matching with TWSE (which uses TPE local dates).
    yf_utc = pd.to_datetime(yf["date"])
    yf["tpe_date"] = yf_utc.dt.tz_convert("Asia/Taipei").dt.normalize().dt.tz_localize(None)

    ticker_no = ticker_tw.replace(".TW", "").replace(".TWO", "")
    twse = fetch_twse_recent(ticker_no, days=days)
    if twse.empty:
        out["skipped_reason"] = "twse empty"
        return out
    twse["date"] = pd.to_datetime(twse["date"]).dt.normalize()

    # Restrict yf to the window for the factor calc. Use the TPE-local
    # date so the merge with TWSE (also TPE-local) lines up correctly.
    cutoff = pd.Timestamp(date.today() - timedelta(days=days))
    yf_window = yf[yf["tpe_date"] >= cutoff][["tpe_date", "close"]].rename(columns={"tpe_date": "date"})

    factor = _derive_adjustment_factor(yf_window, twse, tolerance_pct=0.5)
    if factor is None and not yf_window.empty:
        # If we have any overlap but factor is unstable, abort to avoid
        # writing wrong data. If there's no overlap at all (e.g. gap
        # ticker), default to 1.0 — TWSE bars are raw, but for a brand
        # new ticker there's nothing in Yahoo anyway.
        out["skipped_reason"] = "adjustment factor unstable (split/corp-action in window?)"
        return out
    if factor is None:
        factor = 1.0
    out["adjustment_factor"] = round(factor, 6)

    # Adjust TWSE OHLCV by the factor (so it's on the same scale as Yahoo's
    # split-adjusted silver). Volume is NOT scaled — split adjustments
    # multiply volume by the inverse factor at the split date and all
    # earlier; for a recent 30-day window with no splits in scope, leaving
    # volume unscaled at factor=1 is correct.
    twse_adj = twse.copy()
    for c in ("open", "high", "low", "close"):
        twse_adj[c] = twse_adj[c] * factor

    # Build set of existing TPE-local dates already present in the silver.
    existing_tpe_dates = set(yf["tpe_date"].dt.date)

    # New bars from TWSE need to be stored using the same convention as
    # Yahoo's silver: TPE midnight encoded as UTC (which is 16:00 UTC the
    # PRIOR calendar day). That way the date column stays consistent and
    # the existing `tdConvert("Asia/Taipei").dt.date` access pattern works
    # end-to-end. Concretely: TPE 2026-04-30 -> UTC 2026-04-29 16:00.
    new_rows = []
    overwritten = 0
    for _, r in twse_adj.iterrows():
        tpe_d = r["date"].date()
        utc_ts = pd.Timestamp(tpe_d, tz="Asia/Taipei").tz_convert("UTC")
        new_row = {
            "ticker": ticker_tw,
            "date": utc_ts,
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "adj_close": r["close"],     # TWSE doesn't publish adj close; use close
            "volume": int(r["volume"]) if pd.notna(r["volume"]) else 0,
        }
        if tpe_d in existing_tpe_dates:
            if overwrite_overlap:
                mask = yf["tpe_date"].dt.date == tpe_d
                yf.loc[mask, ["open", "high", "low", "close", "adj_close", "volume"]] = [
                    new_row["open"], new_row["high"], new_row["low"],
                    new_row["close"], new_row["adj_close"], new_row["volume"],
                ]
                overwritten += 1
        else:
            new_rows.append(new_row)

    out["filled_gap"] = len(new_rows)
    out["patched"] = overwritten
    if new_rows:
        yf = pd.concat([yf, pd.DataFrame(new_rows)], ignore_index=True)

    # Drop the helper column before writing.
    if "tpe_date" in yf.columns:
        yf = yf.drop(columns=["tpe_date"])

    yf = (yf.drop_duplicates(subset=["ticker", "date"], keep="last")
            .sort_values("date").reset_index(drop=True))
    yf.to_parquet(silver_path, index=False)
    out["final_rows"] = int(len(yf))
    return out


# ---------------------------------------------------------------------------
# Universe helper
# ---------------------------------------------------------------------------

def _universe_tw_tickers() -> list[str]:
    """Return all .TW tickers from the platform universe registry."""
    try:
        from backend.app.services.universe_registry import read_universe
    except Exception:
        return ["2330.TW", "2303.TW", "2454.TW"]  # fallback
    df = read_universe()
    if df.empty:
        return ["2330.TW", "2303.TW", "2454.TW"]
    rows = df[df["market"] == "TW"]
    rows = rows[rows["ticker"].astype(str).str.len() > 0]
    return [f"{t}.TW" for t in rows["ticker"].astype(str).tolist()]


def patch_all_tw(*, days: int = 30, tickers: Optional[Iterable[str]] = None) -> list[dict]:
    """Run patch_silver_with_twse for every .TW ticker."""
    ts = list(tickers) if tickers else _universe_tw_tickers()
    out = []
    for t in ts:
        try:
            r = patch_silver_with_twse(t, days=days)
        except Exception as e:  # noqa: BLE001
            r = {"ticker": t, "error": str(e)}
        out.append(r)
        print(f"[twse-patch] {t}: {r}", flush=True)
        time.sleep(_TWSE_REQUEST_INTERVAL_S)
    return out


# ---------------------------------------------------------------------------
# Segmented historical patch (handles Taiwan corp-action discontinuities)
# ---------------------------------------------------------------------------
#
# For long-history patches (e.g. 10y), the simple "single global factor"
# approach in `patch_silver_with_twse` is too brittle: any single corp
# action that Yahoo treats differently from TWSE — or any anomalous bar
# — pushes the ratio spread above the 0.5% safety threshold and the
# patch refuses to write.
#
# Segmented patch:
#   1. Walk overlap dates in chronological order and compute ratio = yf/twse.
#   2. Detect step changes (where the ratio jumps by > step_threshold from
#      the running median of the current segment). Each step boundary marks
#      a corp action that adjusts one source but not the other.
#   3. Each segment has its own factor (median of ratios within it).
#   4. For TWSE bars Yahoo is missing, look up the segment containing the
#      bar's date and apply that segment's factor.
#   5. **Validation step**: compute predicted prices for ALL overlap bars
#      using the segment factors; compare to actual Yahoo close. Report
#      max error. If max error > 0.5%, the segment model is suspect.
#
# Empirically for 2330 / 2303 / 2454 over 2016-2026, factor ≈ 1.0 across
# the whole window (yfinance does NOT apply stock-dividend adjustments to
# Taiwan stocks — it treats them as cash dividends only). So the segmented
# code degrades gracefully to a single factor=1.0 segment in practice.
# The segmentation matters mainly for tickers where Yahoo DID apply a
# stock-split adjustment (which we'd see as a step in the ratio).


def _detect_segments(
    overlap_yf: pd.DataFrame,
    overlap_twse: pd.DataFrame,
    *,
    step_threshold_pct: float = 0.3,
    min_segment_len: int = 5,
) -> list[dict]:
    """Detect piecewise-constant segments in the yf/twse close ratio.

    Args:
      overlap_yf:  DataFrame with 'date', 'close' (Yahoo split-adjusted)
      overlap_twse: DataFrame with 'date', 'close' (TWSE raw)
      step_threshold_pct: a bar's ratio that deviates from the running
                          segment median by more than this percent starts
                          a new segment.
      min_segment_len: segments shorter than this are merged into the
                       neighbouring segment to suppress noise.

    Returns list of dicts: [{start_date, end_date, factor, n_bars}, ...]
    """
    merged = overlap_yf[["date", "close"]].merge(
        overlap_twse[["date", "close"]],
        on="date", how="inner", suffixes=("_yf", "_twse"),
    ).sort_values("date").reset_index(drop=True)
    if merged.empty:
        return []
    merged["ratio"] = merged["close_yf"] / merged["close_twse"]
    merged = merged.dropna(subset=["ratio"])
    if merged.empty:
        return []

    segments: list[dict] = []
    cur_start_idx = 0
    cur_ratios = [merged.iloc[0]["ratio"]]

    for i in range(1, len(merged)):
        cur_med = pd.Series(cur_ratios).median()
        cur_ratio = merged.iloc[i]["ratio"]
        deviation_pct = abs(cur_ratio / cur_med - 1) * 100
        if deviation_pct > step_threshold_pct:
            # Close out current segment
            segments.append({
                "start_date": merged.iloc[cur_start_idx]["date"],
                "end_date": merged.iloc[i - 1]["date"],
                "factor": cur_med,
                "n_bars": i - cur_start_idx,
            })
            cur_start_idx = i
            cur_ratios = [cur_ratio]
        else:
            cur_ratios.append(cur_ratio)
    # Final segment
    segments.append({
        "start_date": merged.iloc[cur_start_idx]["date"],
        "end_date": merged.iloc[-1]["date"],
        "factor": pd.Series(cur_ratios).median(),
        "n_bars": len(merged) - cur_start_idx,
    })

    # Merge segments shorter than min_segment_len into neighbours (likely noise)
    if min_segment_len > 1 and len(segments) > 1:
        cleaned: list[dict] = []
        for s in segments:
            if s["n_bars"] < min_segment_len and cleaned:
                # Absorb into previous segment
                cleaned[-1]["end_date"] = s["end_date"]
                cleaned[-1]["n_bars"] += s["n_bars"]
                # Keep cleaned[-1]["factor"] (don't recompute — original was median of a longer span)
            else:
                cleaned.append(dict(s))
        segments = cleaned

    return segments


def _factor_for_date(d: pd.Timestamp, segments: list[dict]) -> float:
    """Return the segment factor for `d`. If `d` is between two segments,
    pick the segment whose end_date is closest before `d`."""
    if not segments:
        return 1.0
    for s in segments:
        if s["start_date"] <= d <= s["end_date"]:
            return s["factor"]
    # Outside any segment — find the closest one
    if d < segments[0]["start_date"]:
        return segments[0]["factor"]
    if d > segments[-1]["end_date"]:
        return segments[-1]["factor"]
    # In a gap between segments — use the one whose end_date is earlier than d
    for i in range(len(segments) - 1):
        if segments[i]["end_date"] < d <= segments[i + 1]["start_date"]:
            # `d` is in the gap. Use the post-gap factor (after the corp action).
            return segments[i + 1]["factor"]
    return segments[-1]["factor"]


def historical_patch_with_segments(
    ticker_tw: str,
    *,
    days: int = 3650,
    overwrite_overlap: bool = False,
    step_threshold_pct: float = 0.3,
) -> dict:
    """Long-window TWSE patch with per-segment adjustment factors.

    Differs from `patch_silver_with_twse` (the nightly 30-day path):
    - Uses segmented factor detection (handles Taiwan stock-dividend
      events that change the yf/twse ratio mid-window).
    - Only fills GAPS by default (overwrite_overlap=False) — preserves
      Yahoo's data on overlap dates.
    - Validates: predicts prices for overlap bars using segments,
      reports max error. If error is small, the segment model is sound.
    """
    out: dict = {
        "ticker": ticker_tw, "filled_gap": 0, "patched_overlap": 0,
        "segments": [], "validation_max_error_pct": None,
        "skipped_reason": None,
    }

    silver_path = PRICES_DIR / f"{ticker_tw}.parquet"
    if not silver_path.exists():
        out["skipped_reason"] = "silver missing"
        return out

    yf = pd.read_parquet(silver_path)
    yf_tpe = pd.to_datetime(yf["date"]).dt.tz_convert("Asia/Taipei").dt.normalize().dt.tz_localize(None)
    yf["tpe_date"] = yf_tpe

    ticker_no = ticker_tw.replace(".TW", "").replace(".TWO", "")
    print(f"[hist-patch] {ticker_tw}: fetching {days}d of TWSE direct ...", flush=True)

    # Fetch all months covering the window (slowly — TWSE rate-limits)
    today = date.today()
    months_back = max(1, (days + 30) // 28)
    months: list[tuple[int, int]] = []
    y, m = today.year, today.month
    for _ in range(months_back + 1):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months = sorted(set(months))

    pieces: list[pd.DataFrame] = []
    for (yi, mi) in months:
        df = fetch_twse_month(ticker_no, yi, mi)
        if not df.empty:
            pieces.append(df)
        time.sleep(_TWSE_HIST_INTERVAL_S)
    if not pieces:
        out["skipped_reason"] = "twse empty"
        return out
    twse = pd.concat(pieces, ignore_index=True)
    twse["date"] = pd.to_datetime(twse["date"]).dt.normalize()
    cutoff = pd.Timestamp(today - timedelta(days=days))
    twse = twse[twse["date"] >= cutoff].sort_values("date").reset_index(drop=True)
    print(f"[hist-patch] {ticker_tw}: TWSE rows fetched: {len(twse)}", flush=True)

    # Detect segments using the TPE-aligned overlap.
    overlap_yf = yf[["tpe_date", "close"]].rename(columns={"tpe_date": "date"})
    segs = _detect_segments(overlap_yf, twse, step_threshold_pct=step_threshold_pct)
    out["segments"] = [
        {
            "start": str(s["start_date"].date()),
            "end": str(s["end_date"].date()),
            "factor": round(s["factor"], 6),
            "n_bars": s["n_bars"],
        }
        for s in segs
    ]
    print(f"[hist-patch] {ticker_tw}: detected {len(segs)} factor segment(s):",
          [(s['start'], s['end'], s['factor'], s['n_bars']) for s in out['segments']],
          flush=True)

    # Validation: predict yahoo close for each overlap bar via segment factor.
    merged = overlap_yf.merge(
        twse[["date", "close"]], on="date", how="inner", suffixes=("_yf", "_twse"),
    )
    if not merged.empty:
        merged["predicted_yf"] = merged.apply(
            lambda r: r["close_twse"] * _factor_for_date(r["date"], segs), axis=1,
        )
        err_pct = (merged["predicted_yf"] - merged["close_yf"]).abs() / merged["close_yf"] * 100
        out["validation_max_error_pct"] = float(err_pct.max())
        out["validation_p99_error_pct"] = float(err_pct.quantile(0.99))
        out["validation_median_error_pct"] = float(err_pct.median())
        # Show worst offenders
        worst = merged.assign(err_pct=err_pct).nlargest(3, "err_pct")
        out["worst_overlap_dates"] = [
            {"date": str(r["date"].date()),
             "yf_close": round(r["close_yf"], 4),
             "twse_close": round(r["close_twse"], 4),
             "predicted": round(r["predicted_yf"], 4),
             "err_pct": round(r["err_pct"], 4)}
            for _, r in worst.iterrows()
        ]
        print(f"[hist-patch] {ticker_tw}: validation max_err={out['validation_max_error_pct']:.4f}% "
              f"p99={out['validation_p99_error_pct']:.4f}% "
              f"median={out['validation_median_error_pct']:.4f}%", flush=True)

    # Now actually patch. For each TWSE bar Yahoo is missing, apply the
    # segment factor and insert.
    existing_tpe_dates = set(yf["tpe_date"].dt.date)
    new_rows = []
    overwritten = 0
    for _, r in twse.iterrows():
        d_ts = r["date"]
        tpe_d = d_ts.date()
        factor = _factor_for_date(d_ts, segs)
        utc_ts = pd.Timestamp(tpe_d, tz="Asia/Taipei").tz_convert("UTC")
        adj = {
            "ticker": ticker_tw,
            "date": utc_ts,
            "open": r["open"] * factor if pd.notna(r["open"]) else None,
            "high": r["high"] * factor if pd.notna(r["high"]) else None,
            "low": r["low"] * factor if pd.notna(r["low"]) else None,
            "close": r["close"] * factor,
            "adj_close": r["close"] * factor,
            "volume": int(r["volume"]) if pd.notna(r["volume"]) else 0,
        }
        if tpe_d in existing_tpe_dates:
            if overwrite_overlap:
                mask = yf["tpe_date"].dt.date == tpe_d
                yf.loc[mask, ["open", "high", "low", "close", "adj_close", "volume"]] = [
                    adj["open"], adj["high"], adj["low"],
                    adj["close"], adj["adj_close"], adj["volume"],
                ]
                overwritten += 1
        else:
            new_rows.append(adj)

    out["filled_gap"] = len(new_rows)
    out["patched_overlap"] = overwritten
    if new_rows:
        yf = pd.concat([yf, pd.DataFrame(new_rows)], ignore_index=True)

    if "tpe_date" in yf.columns:
        yf = yf.drop(columns=["tpe_date"])

    yf = (yf.drop_duplicates(subset=["ticker", "date"], keep="last")
            .sort_values("date").reset_index(drop=True))
    yf.to_parquet(silver_path, index=False)
    out["final_rows"] = int(len(yf))
    return out


def historical_patch_all_tw(
    *,
    days: int = 3650,
    tickers: Optional[Iterable[str]] = None,
    step_threshold_pct: float = 0.3,
) -> list[dict]:
    """Run historical_patch_with_segments for every .TW ticker."""
    ts = list(tickers) if tickers else _universe_tw_tickers()
    out = []
    for t in ts:
        try:
            r = historical_patch_with_segments(
                t, days=days, step_threshold_pct=step_threshold_pct
            )
        except Exception as e:  # noqa: BLE001
            r = {"ticker": t, "error": str(e)}
        out.append(r)
        print(f"[hist-patch] {t} done: filled_gap={r.get('filled_gap')} "
              f"max_err={r.get('validation_max_error_pct')}", flush=True)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="TWSE direct prices patcher")
    p.add_argument("--check", action="store_true",
                   help="Run sanity check only (no writes)")
    p.add_argument("--patch", action="store_true",
                   help="Run nightly 30-day patch (writes; overwrites overlap)")
    p.add_argument("--historical", action="store_true",
                   help="Run segmented historical patch (gap-fill only by default)")
    p.add_argument("--tickers",
                   help="Comma-separated .TW tickers; defaults to universe TW set")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--step-threshold-pct", type=float, default=0.3,
                   help="ratio jump threshold for segment detection (historical only)")
    args = p.parse_args()

    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else _universe_tw_tickers()
    )

    # Default to a sanity check ONLY if no other action was requested.
    # Avoids running an expensive 30-day-per-ticker check before --historical.
    run_default_check = not (args.check or args.patch or args.historical)
    if args.check or run_default_check:
        # Auto-check should sample only 30 days (cheap), regardless of --days
        check_days = args.days if args.check else 30
        print(f"[twse-check] tickers: {tickers}  days={check_days}")
        for t in tickers:
            r = sanity_check(t, days=check_days)
            print(f"  {t}: overlap={r.get('overlap_dates')}  "
                  f"yahoo_only={len(r.get('yahoo_only_dates', []))}  "
                  f"twse_only={len(r.get('twse_only_dates', []))}  "
                  f"ratio_med={r.get('close_ratio_median')}  "
                  f"max_diff_pct={r.get('max_close_diff_pct')}")
            if r.get('twse_only_dates'):
                print(f"    twse_only sample: {r['twse_only_dates'][:5]}")
        if args.check:
            return 0

    if args.patch:
        results = patch_all_tw(days=args.days, tickers=tickers)
        n_ok = sum(1 for r in results if "error" not in r and not r.get("skipped_reason"))
        n_err = sum(1 for r in results if "error" in r or r.get("skipped_reason"))
        print(f"[twse-patch] done: ok={n_ok} err/skipped={n_err}")
        return 1 if n_err == len(results) else 0

    if args.historical:
        results = historical_patch_all_tw(
            days=args.days, tickers=tickers,
            step_threshold_pct=args.step_threshold_pct,
        )
        print()
        print("=" * 70)
        print("HISTORICAL PATCH SUMMARY")
        print("=" * 70)
        for r in results:
            if "error" in r:
                print(f"  {r['ticker']}: ERROR - {r['error']}")
                continue
            segs = r.get("segments", [])
            print(f"  {r['ticker']}: gaps_filled={r.get('filled_gap', 0)}  "
                  f"final_rows={r.get('final_rows')}  "
                  f"validation max_err={r.get('validation_max_error_pct'):.4f}%  "
                  f"segments={len(segs)}")
            if r.get("validation_max_error_pct") and r["validation_max_error_pct"] > 0.5:
                print(f"    WARN: max validation error exceeds 0.5% — segment model may be off")
                for w in r.get("worst_overlap_dates", []):
                    print(f"      worst: {w}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
