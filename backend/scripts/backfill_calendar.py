"""
Backfill the earnings calendar from existing on-disk sources.

Sources (in order):
  1. backend/data/earnings_releases/ticker=*.parquet
       18 US tickers' 8-K Item 2.02 filings (the post-event press releases).
       Each row -> one calendar event with status='done', source='edgar_8k'.

  2. backend/data/taiwan/material_info/data.parquet  (when present)
       Taiwan MOPS material-information disclosures filtered for earnings-call
       notification subjects -> calendar events with source='mops_material_info'.

The fiscal_period for each event is resolved against the calculated-layer
topline parquet (`filing_data/calculated/ticker=*.parquet`), which already
has the canonical fiscal_year / fiscal_quarter labels per period_end. Falls
back to the period_of_report date string if the topline isn't built yet.

Idempotent: re-running upserts existing rows without duplication.

Run:
    python -m backend.scripts.backfill_calendar
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

# Skill / CLAUDE.md: ASCII-only print/log statements.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("backfill_calendar")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.storage import (  # noqa: E402
    upsert_events,
    UpsertStats,
)

_DATA = PROJECT_ROOT / "backend" / "data"
_RELEASES_DIR = _DATA / "earnings_releases"
_CALC_DIR = _DATA / "filing_data" / "calculated"
_TAIWAN_MI = _DATA / "taiwan" / "material_info" / "data.parquet"


# ---------------------------------------------------------------------------
# Fiscal-period resolver
# ---------------------------------------------------------------------------

def _build_fiscal_map(ticker: str) -> list[tuple[pd.Timestamp, str]]:
    """Per-ticker (end_date -> fiscal_label) list, ascending. Mirrors the
    helper in api/routers/v1/earnings.py but kept local to avoid coupling."""
    p = _CALC_DIR / f"ticker={ticker}.parquet"
    if not p.exists():
        return []
    try:
        df = pd.read_parquet(p, columns=["end_date", "fiscal_year", "fiscal_quarter", "is_ytd"])
    except Exception:
        return []
    df = df[
        df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
        & (~df["is_ytd"].astype(bool))
        & df["fiscal_year"].notna()
        & df["end_date"].notna()
    ].copy()
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date")
    return [
        (r["end_date"], f"FY{int(r['fiscal_year'])}-{r['fiscal_quarter']}")
        for _, r in df.iterrows()
        if pd.notna(r["fiscal_year"])
    ]


def _fiscal_period_for(
    event_date: pd.Timestamp,
    fmap: list[tuple[pd.Timestamp, str]],
    *,
    max_lookback_days: int = 120,
) -> str | None:
    """Map an 8-K event date (period_of_report or filing_date) to the most
    recent fiscal quarter that closed BEFORE the event.

    Earnings 8-Ks are filed 2-8 weeks after the quarter ends, so the
    quarter we're reporting on is the latest fmap entry with end_date <=
    event_date. Bounded by max_lookback_days to avoid mis-matching a stray
    8-K (e.g. a year-late restatement) to an irrelevant quarter."""
    if not fmap:
        return None
    candidate: str | None = None
    candidate_end: pd.Timestamp | None = None
    for end_date, label in fmap:
        if end_date <= event_date:
            candidate, candidate_end = label, end_date
        else:
            break
    if candidate is None or candidate_end is None:
        return None
    if (event_date - candidate_end) > pd.Timedelta(days=max_lookback_days):
        return None
    return candidate


# ---------------------------------------------------------------------------
# US ingest from earnings_releases parquets
# ---------------------------------------------------------------------------

# 8-K item codes that indicate earnings-results filings.
_EARNINGS_ITEMS = {"2.02", "2.02 ", "Results of Operations and Financial Condition"}


def _row_is_earnings_release(row: pd.Series) -> bool:
    """Item 2.02 = Results of Operations. Some filings list multiple items."""
    items_str = str(row.get("items") or "")
    return "2.02" in items_str


def ingest_us_earnings_releases() -> list[dict]:
    """Read every earnings_releases parquet and convert rows to calendar
    event dicts. Returns the list of events ready for upsert_events()."""
    if not _RELEASES_DIR.exists():
        log.warning("No earnings_releases dir at %s", _RELEASES_DIR)
        return []

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (ticker, fiscal_period) dedup

    for parquet in sorted(_RELEASES_DIR.glob("ticker=*.parquet")):
        ticker = parquet.stem.replace("ticker=", "")
        try:
            df = pd.read_parquet(parquet)
        except Exception as exc:
            log.warning("[%s] failed to read parquet: %s", ticker, exc)
            continue

        df = df[df.apply(_row_is_earnings_release, axis=1)]
        if df.empty:
            log.info("[%s] no Item 2.02 rows", ticker)
            continue

        fmap = _build_fiscal_map(ticker)
        log.info("[%s] %d earnings-release rows; fiscal_map size=%d",
                 ticker, len(df), len(fmap))

        for _, r in df.iterrows():
            try:
                period = pd.to_datetime(r["period_of_report"])
                filed  = pd.to_datetime(r["filing_date"])
            except Exception:
                continue
            if pd.isna(period) or pd.isna(filed):
                continue

            fiscal_period = _fiscal_period_for(period, fmap)
            if not fiscal_period:
                # Fallback when topline isn't built for this ticker yet:
                # synthetic label from the period_of_report year+month.
                fiscal_period = f"AS-OF-{period.strftime('%Y-%m-%d')}"

            key = (ticker, fiscal_period)
            if key in seen:
                continue
            seen.add(key)

            # The 8-K filing_date is when the press-release was filed with
            # the SEC -- typically same day as the earnings call but a few
            # hours after. Without a more precise time, store filing_date
            # at midnight UTC and let the LLM enrichment pass refine it
            # later when it has time-of-day data.
            release_dt = pd.Timestamp(filed.date(), tz="UTC")

            out.append({
                "ticker": ticker,
                "market": "US",
                "fiscal_period": fiscal_period,
                "release_datetime_utc": release_dt,
                "release_local_tz": "America/New_York",
                "status": "done",
                "press_release_url": str(r.get("url") or "") or None,
                "filing_url": str(r.get("url") or "") or None,
                "webcast_url": None,
                "transcript_url": None,
                "dial_in_phone": None,
                "dial_in_pin": None,
                "source": "edgar_8k",
                "source_id": str(r.get("accession_no") or ""),
            })

    return out


# ---------------------------------------------------------------------------
# Taiwan ingest from material_info
# ---------------------------------------------------------------------------

# MOPS material-info subjects that indicate an earnings-call notification.
# These are heuristic substrings; we'll refine as we see real data.
_TW_EARNINGS_SUBJECT_HINTS = (
    "法人說明會",   # institutional briefing (most common)
    "重大訊息",     # major information notice (broad; we narrow by other fields if needed)
)


def ingest_taiwan_material_info() -> list[dict]:
    """Read taiwan/material_info/data.parquet and emit one event per
    'institutional-briefing' style notification."""
    if not _TAIWAN_MI.exists():
        log.info("Taiwan material_info parquet not present, skipping")
        return []

    df = pd.read_parquet(_TAIWAN_MI)
    if df.empty:
        return []

    # Filter to earnings-call style subjects
    subj = df.get("subject")
    if subj is None:
        log.info("material_info has no 'subject' column, skipping")
        return []
    mask = subj.astype(str).apply(
        lambda s: any(h in s for h in _TW_EARNINGS_SUBJECT_HINTS)
    )
    df = df[mask]
    if df.empty:
        log.info("No earnings-call style rows in Taiwan material_info")
        return []

    out: list[dict] = []
    for _, r in df.iterrows():
        try:
            ann_dt = pd.to_datetime(r.get("announcement_datetime"))
        except Exception:
            continue
        if pd.isna(ann_dt):
            continue
        # Use fiscal_ym_guess if present, else mark as AS-OF the announcement date.
        fy_guess = str(r.get("fiscal_ym_guess") or "").strip()
        fiscal_period = fy_guess if fy_guess else f"AS-OF-{ann_dt.strftime('%Y-%m')}"
        ticker = str(r.get("ticker") or "")
        if not ticker:
            continue
        out.append({
            "ticker": ticker,
            "market": "TW",
            "fiscal_period": fiscal_period,
            "release_datetime_utc": pd.Timestamp(ann_dt, tz="UTC"),
            "release_local_tz": "Asia/Taipei",
            "status": "done",
            "press_release_url": None,
            "filing_url": None,
            "webcast_url": None,
            "transcript_url": None,
            "dial_in_phone": None,
            "dial_in_pin": None,
            "source": "mops_material_info",
            "source_id": str(r.get("content_hash") or ""),
        })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    log.info("backfill_calendar starting")
    us_events = ingest_us_earnings_releases()
    log.info("US events: %d", len(us_events))
    tw_events = ingest_taiwan_material_info()
    log.info("TW events: %d", len(tw_events))

    all_events = us_events + tw_events
    if not all_events:
        log.warning("No events to upsert.")
        return 0

    stats: UpsertStats = upsert_events(all_events)
    log.info("Upsert stats: inserted=%d updated=%d touched=%d",
             stats.inserted, stats.updated, stats.touched)

    # Sanity summary
    from backend.app.services.calendar.storage import read_events
    df = read_events()
    log.info("events.parquet now has %d rows; markets: %s",
             len(df), df["market"].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
