"""
Refresh the US earnings calendar — daily orchestration.

Pipeline:
  1. Fetch NASDAQ calendar for the next N days (primary source).
  2. Filter to tickers in our platform_universe.csv (US market only).
  3. Refine fiscal_period for each event using the ticker's calculated-layer
     fiscal_map (so MSFT's "Mar/2026" becomes "FY2026-Q3", not Q1).
  4. Fetch Yahoo Finance next-earnings-date per universe ticker (verification).
  5. Cross-reference: annotate each event with `verification` field.
  6. Upsert to events.parquet.

Cache-first rule (CLAUDE.md): NASDAQ raw JSON and Yahoo per-ticker dicts
are persisted to backend/data/_raw/ before any parsing. Re-runs read from
disk where possible.

Run:
    python -m backend.scripts.refresh_calendar_us
    python -m backend.scripts.refresh_calendar_us --days 14 --no-yahoo

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.calendar.storage import (  # noqa: E402
    upsert_events,
    UpsertStats,
)
from backend.app.services.universe_registry import read_universe  # noqa: E402

# scrapers live under tools/, also on the path now
from tools.web_scraper.nasdaq_earnings_calendar import (  # noqa: E402
    fetch_range as nasdaq_fetch_range,
)
from tools.web_scraper.yahoo_earnings_calendar import (  # noqa: E402
    fetch_next_dates as yahoo_fetch_next_dates,
    cross_reference as yahoo_cross_reference,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("refresh_calendar_us")

_CALC_DIR = PROJECT_ROOT / "backend" / "data" / "filing_data" / "calculated"


# ---------------------------------------------------------------------------
# Universe filter
# ---------------------------------------------------------------------------

def _us_universe_tickers() -> set[str]:
    """Return the set of US tickers from platform_universe.csv."""
    df = read_universe()
    if df.empty:
        return set()
    us = df[df["market"].astype(str).str.upper() == "US"]
    return set(us["ticker"].astype(str).str.upper().tolist())


# ---------------------------------------------------------------------------
# Fiscal-period refinement
# ---------------------------------------------------------------------------

def _build_fiscal_map(ticker: str) -> list[tuple[pd.Timestamp, str]]:
    """Build (end_date -> fiscal_label) list from ticker's calculated parquet.
    Mirrors the helper in earnings.py / backfill_calendar.py."""
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


def _refine_fiscal_period(event: dict, fmap: list[tuple[pd.Timestamp, str]]) -> str:
    """Given an event with a NASDAQ-derived (calendar-year approximate)
    fiscal_period, replace it with the ticker's actual upcoming fiscal
    quarter by extending the fmap forward by one quarter from the latest
    known reported quarter.

    Strategy: take the latest fiscal_period in fmap and step it forward by
    the number of quarters between that quarter's end_date and the
    NASDAQ event date.
    """
    if not fmap:
        return event.get("fiscal_period") or ""

    nasdaq_dt = event.get("release_datetime_utc")
    if not isinstance(nasdaq_dt, datetime):
        return event.get("fiscal_period") or ""

    # Find the most recent fiscal_quarter end_date strictly BEFORE the NASDAQ
    # event (the prior reported quarter). The upcoming earnings call reports
    # the quarter that ENDED most recently before the call, which is the one
    # AFTER the latest entry in fmap.
    nasdaq_pd = pd.Timestamp(nasdaq_dt).tz_localize(None) if pd.Timestamp(nasdaq_dt).tz is not None else pd.Timestamp(nasdaq_dt)
    latest_end, latest_label = fmap[-1]
    if nasdaq_pd <= latest_end + pd.Timedelta(days=15):
        # NASDAQ event is barely past the latest known quarter -- they're
        # reporting that one.
        return latest_label

    # Step forward: count quarters between latest_end and nasdaq_pd
    quarters_forward = max(
        1,
        round((nasdaq_pd - latest_end).days / 91.25),  # avg quarter length
    )
    fy_str = latest_label.split("-Q")[0].lstrip("FY")
    q_str = latest_label.split("-Q")[1]
    try:
        fy = int(fy_str)
        q = int(q_str)
    except ValueError:
        return event.get("fiscal_period") or latest_label

    total = fy * 4 + (q - 1) + quarters_forward
    new_fy = total // 4
    new_q = (total % 4) + 1
    return f"FY{new_fy}-Q{new_q}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Refresh US earnings calendar")
    p.add_argument("--days", type=int, default=21,
                   help="Days forward to fetch (default 21)")
    p.add_argument("--no-yahoo", action="store_true",
                   help="Skip Yahoo verification pass")
    p.add_argument("--no-cache", action="store_true",
                   help="Force re-fetch NASDAQ instead of reading bronze cache")
    args = p.parse_args()

    universe = _us_universe_tickers()
    log.info("US universe: %d tickers", len(universe))
    if not universe:
        log.warning("Empty US universe -- check platform_universe.csv")
        return 1

    # 1. NASDAQ fetch
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=args.days)
    log.info("Fetching NASDAQ calendar %s -> %s", start.date(), end.date())
    all_events = nasdaq_fetch_range(start, end, use_cache=not args.no_cache)
    log.info("NASDAQ raw events: %d", len(all_events))

    # 2. Filter to universe
    events = [e for e in all_events if e.get("ticker") in universe]
    log.info("After universe filter: %d events for %d distinct tickers",
             len(events), len({e["ticker"] for e in events}))

    # 3. Refine fiscal_period using each ticker's fmap
    fmaps: dict[str, list] = {}
    for ev in events:
        tkr = ev["ticker"]
        if tkr not in fmaps:
            fmaps[tkr] = _build_fiscal_map(tkr)
        old_fp = ev["fiscal_period"]
        new_fp = _refine_fiscal_period(ev, fmaps[tkr])
        if new_fp and new_fp != old_fp:
            ev["fiscal_period"] = new_fp

    # 4. Yahoo verification (per universe ticker that NASDAQ surfaced)
    if not args.no_yahoo:
        affected = sorted({e["ticker"] for e in events})
        log.info("Yahoo verification for %d tickers", len(affected))
        yahoo_dates = yahoo_fetch_next_dates(affected)
        events = yahoo_cross_reference(events, yahoo_dates)
    else:
        for ev in events:
            ev["verification"] = "nasdaq_only"

    # 5. Strip private fields before upsert (keys starting with underscore)
    for ev in events:
        for k in list(ev.keys()):
            if k.startswith("_"):
                del ev[k]

    if not events:
        log.warning("No US universe events to upsert.")
        return 0

    # 6. Upsert
    stats: UpsertStats = upsert_events(events)
    log.info("Upsert stats: inserted=%d updated=%d touched=%d",
             stats.inserted, stats.updated, stats.touched)

    # 7. Verification breakdown summary
    from collections import Counter
    verif_counts = Counter(e.get("verification") for e in events)
    log.info("Verification breakdown:")
    for label, cnt in verif_counts.most_common():
        log.info("  %-22s %d", label or "<empty>", cnt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
