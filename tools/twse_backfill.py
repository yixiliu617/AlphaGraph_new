"""
One-shot historical backfill for Taiwan monthly revenue via TWSE open data.

Downloads C04003 ZIPs for a year range, parses each XLS, filters to the
watchlist, upserts into the parquet store. ZIPs are disk-cached so
re-runs and partial failures are cheap to resume.

Usage:
    # default: 10 years back to the current year/month
    python tools/twse_backfill.py

    # custom range
    python tools/twse_backfill.py --start 2016-01 --end 2026-03

    # use a fresh scratch data_dir instead of writing to backend/data/taiwan/
    python tools/twse_backfill.py --data-dir /tmp/taiwan-backfill
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.app.services.taiwan.scrapers.twse_historical import backfill_range
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    upsert_monthly_revenue,
)
from backend.app.services.taiwan.registry import list_watchlist_tickers


def _parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=_parse_ym, default=None,
                   help="YYYY-MM inclusive start month (default: 10 years ago)")
    p.add_argument("--end", type=_parse_ym, default=None,
                   help="YYYY-MM inclusive end month (default: current TPE month)")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                   help=f"Parquet data dir (default: {DEFAULT_DATA_DIR})")
    p.add_argument("--cache-dir", type=Path, default=None,
                   help="ZIP cache dir (default: <data-dir>/_raw/twse_zip)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    now_tpe = datetime.now(ZoneInfo("Asia/Taipei"))
    # TWSE publishes the prior month's file by mid-month; the current month
    # is typically unavailable. Default end = previous month.
    default_end = (now_tpe.year, now_tpe.month - 1) if now_tpe.month > 1 else (now_tpe.year - 1, 12)
    end = args.end or default_end
    start = args.start or (end[0] - 10, end[1])
    cache = args.cache_dir or (args.data_dir / "_raw" / "twse_zip")

    watchlist = set(list_watchlist_tickers())
    print(f"[backfill] range={start[0]:04d}-{start[1]:02d} .. {end[0]:04d}-{end[1]:02d}  "
          f"watchlist={len(watchlist)} tickers  data_dir={args.data_dir}  cache={cache}")

    progress = {"ok": 0, "skipped": 0, "failed": 0, "rows": 0}

    def _cb(year: int, month: int, n: int) -> None:
        if n < 0:
            progress["failed"] += 1
            tag = "FAIL"
        elif n == 0:
            progress["skipped"] += 1
            tag = "empty"
        else:
            progress["ok"] += 1
            progress["rows"] += n
            tag = f"{n:3d} rows"
        print(f"  {year:04d}-{month:02d}  {tag}")

    rows = backfill_range(
        start=start, end=end, watchlist=watchlist, cache_dir=cache, on_progress=_cb,
    )
    print(f"[backfill] downloaded: {progress['ok']} months OK, "
          f"{progress['skipped']} empty, {progress['failed']} failed. "
          f"watchlist rows = {len(rows)}")

    if not rows:
        print("[backfill] no rows to upsert — aborting")
        return 1

    stats = upsert_monthly_revenue(rows, data_dir=args.data_dir)
    print(f"[backfill] upsert stats: inserted={stats.inserted} touched={stats.touched} amended={stats.amended}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
