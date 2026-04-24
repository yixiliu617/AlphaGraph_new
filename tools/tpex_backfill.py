"""
One-shot TPEx historical backfill for 上櫃 monthly revenue.

Data source: https://www.tpex.org.tw/zh-tw/mainboard/listed/month/revenue.html
    per-month XLS at /storage/statistic/sales_revenue/O_{YYYYMM}.xls

Archive coverage: 2009-12 → prior month (earlier months return HTTP 302).

Usage:
    # default: from 2009-12 to last full TPE month, into backend/data/taiwan/
    python tools/tpex_backfill.py

    # custom range
    python tools/tpex_backfill.py --start 2015-01 --end 2026-03

    # scratch dir + Emerging (興櫃 'U' prefix)
    python tools/tpex_backfill.py --prefix U --data-dir /tmp/tpex-test
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.app.services.taiwan.scrapers.tpex_historical import backfill_range
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
    p.add_argument("--start", type=_parse_ym, default=(2009, 12),
                   help="YYYY-MM inclusive start (default: 2009-12)")
    p.add_argument("--end", type=_parse_ym, default=None,
                   help="YYYY-MM inclusive end (default: prior full month TPE)")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--cache-dir", type=Path, default=None,
                   help="Cache dir (default: <data-dir>/_raw/tpex_xls)")
    p.add_argument("--prefix", default="O", choices=["O", "U"],
                   help="'O'=上櫃 (default), 'U'=興櫃 Emerging")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    now_tpe = datetime.now(ZoneInfo("Asia/Taipei"))
    default_end = (now_tpe.year, now_tpe.month - 1) if now_tpe.month > 1 else (now_tpe.year - 1, 12)
    end = args.end or default_end
    cache = args.cache_dir or (args.data_dir / "_raw" / "tpex_xls")

    watchlist = set(list_watchlist_tickers())
    market_label = "上櫃" if args.prefix == "O" else "興櫃"
    print(f"[tpex-backfill] {market_label} prefix={args.prefix} "
          f"range={args.start[0]:04d}-{args.start[1]:02d} .. {end[0]:04d}-{end[1]:02d}  "
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
        start=args.start, end=end, watchlist=watchlist,
        cache_dir=cache, prefix=args.prefix, on_progress=_cb,
    )
    print(f"[tpex-backfill] {progress['ok']} months OK, "
          f"{progress['failed']} failed. watchlist rows = {len(rows)}")

    if not rows:
        print("[tpex-backfill] nothing to upsert")
        return 1

    stats = upsert_monthly_revenue(rows, data_dir=args.data_dir)
    print(f"[tpex-backfill] upsert stats: inserted={stats.inserted} "
          f"touched={stats.touched} amended={stats.amended}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
