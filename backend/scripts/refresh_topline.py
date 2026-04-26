"""
Daily EDGAR topline refresh.

Invokes ToplineBuilder.refresh() across the full universe — detects new
10-K / 10-Q / 10-K/A / 10-Q/A filings since the last run (via _filing_state.json)
and rebuilds only tickers whose accession numbers changed. A `.refresh.lock`
in backend/data/filing_data/topline/ prevents overlapping runs if a prior
invocation is still in flight.

Usage:
    python -m backend.scripts.refresh_topline              # all tickers
    python -m backend.scripts.refresh_topline --force      # rebuild everything
    python -m backend.scripts.refresh_topline --tickers NVDA MU AAPL

Scheduled via AlphaGraph_EdgarDaily Windows task — daily @ 06:00 Asia/Taipei
(= 6pm Eastern Daylight during US DST; 5pm Eastern during winter).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from backend.app.services.data_agent.topline_builder import ToplineBuilder


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily EDGAR topline refresh")
    ap.add_argument(
        "--tickers",
        nargs="*",
        default=None,
        help="Restrict to these tickers (default: whole universe from config)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Rebuild every ticker even when no new filing is detected",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("refresh_topline")

    builder = ToplineBuilder()
    log.info(
        "refresh_topline starting tickers=%s force=%s",
        args.tickers if args.tickers else "ALL",
        args.force,
    )
    report = builder.refresh(tickers=args.tickers, force=args.force)

    if "error" in report:
        log.error("refresh aborted: %s", report["error"])
        return 1

    tickers = report.get("tickers", {}) or {}
    rebuilt = sum(1 for v in tickers.values() if v.get("rebuilt"))
    unchanged = len(tickers) - rebuilt
    log.info(
        "refresh_topline done: %d ticker(s) rebuilt, %d unchanged",
        rebuilt, unchanged,
    )

    # Per-ticker one-line summary for the scheduled log.
    for ticker, result in tickers.items():
        if result.get("rebuilt"):
            log.info("  %s rebuilt (amendment=%s)",
                     ticker, result.get("is_amendment_update", False))
        elif "error" in result:
            log.warning("  %s ERROR: %s", ticker, result["error"])

    # Dump the full report as JSON on the last line for machine parsing.
    sys.stdout.write(json.dumps(report, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
