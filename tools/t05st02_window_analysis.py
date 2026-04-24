"""
Fetch MOPS t05st02 (material information / 重大訊息) for each day of
the April 2026 revenue-publication window and analyze:
  - total announcements per day
  - how many are monthly-revenue flavored
  - how many of our 51 watchlist tickers appeared
  - distribution of filing timing (what time on what day)

This tells us whether material-info polling is a viable live signal
for monthly revenue, vs. polling the structured t146sb05_detail API.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Keywords that indicate a monthly-revenue announcement.
# Observed patterns: "公告本公司2026年3月合併營業額"
#                    "公告本公司○年○月份營業收入"
#                    "公告本公司自結○月份合併營收"
_REVENUE_KEYWORDS = (
    "營業額",  # revenue / sales (most common)
    "營業收入",  # operating revenue
    "月份營收",  # monthly revenue
    "自結",  # self-computed (often prepended to revenue announcements)
    "合併營收",  # consolidated revenue
)


def main() -> None:
    from backend.app.services.taiwan.mops_client import MopsClient
    from backend.app.services.taiwan.registry import list_watchlist_tickers

    watchlist = set(list_watchlist_tickers())
    print(f"[config] watchlist size: {len(watchlist)}")

    # April 2026 publication window (民國 115 / 4 / 1..11 + a comparison day)
    roc_year, month = 115, 4
    days = list(range(1, 12))

    daily_stats: list[dict] = []
    tickers_with_revenue: set[str] = set()
    all_revenue_events: list[tuple] = []

    with MopsClient() as c:
        for day in days:
            res = c.post_json(
                "/mops/api/t05st02",
                {"year": str(roc_year), "month": f"{month:02d}", "day": f"{day:02d}"},
            )
            if res.status_code != 200:
                print(f"  {roc_year:03d}-{month:02d}-{day:02d}: HTTP {res.status_code}")
                continue
            body = res.json()
            if body.get("code") != 200:
                print(f"  {roc_year:03d}-{month:02d}-{day:02d}: api code={body.get('code')}")
                continue

            rows = body.get("result", {}).get("data", []) or []
            revenue_rows = [r for r in rows if any(k in (r[4] if len(r) > 4 else "") for k in _REVENUE_KEYWORDS)]
            watchlist_rows = [r for r in rows if len(r) > 2 and r[2] in watchlist]
            watchlist_rev = [r for r in rows
                             if len(r) > 4
                             and r[2] in watchlist
                             and any(k in r[4] for k in _REVENUE_KEYWORDS)]

            for r in revenue_rows:
                ticker = r[2] if len(r) > 2 else ""
                tickers_with_revenue.add(ticker)
                all_revenue_events.append(tuple(r[:5]))

            daily_stats.append({
                "day": day, "total": len(rows),
                "revenue_all": len(revenue_rows),
                "watchlist_all": len(watchlist_rows),
                "watchlist_revenue": len(watchlist_rev),
            })

            print(f"  {roc_year:03d}/{month:02d}/{day:02d}: "
                  f"total={len(rows):4d}  revenue={len(revenue_rows):4d}  "
                  f"watchlist_any={len(watchlist_rows):3d}  watchlist_revenue={len(watchlist_rev):3d}")

    print("\n=== Summary ===")
    total = sum(s["total"] for s in daily_stats)
    total_rev = sum(s["revenue_all"] for s in daily_stats)
    total_wl = sum(s["watchlist_all"] for s in daily_stats)
    total_wl_rev = sum(s["watchlist_revenue"] for s in daily_stats)
    print(f"Days queried: {len(daily_stats)}")
    print(f"Total announcements (all tickers, all types): {total:,}")
    print(f"Revenue-flavored announcements: {total_rev:,}  ({100*total_rev/max(total,1):.1f}%)")
    print(f"Watchlist announcements (any type): {total_wl}")
    print(f"Watchlist revenue announcements: {total_wl_rev}")
    print(f"Unique tickers with ≥1 revenue announcement in window: {len(tickers_with_revenue):,}")

    # Peek some examples
    print("\nSample revenue announcements (first 10):")
    for ev in all_revenue_events[:10]:
        date_, t_, ticker, name, subj = ev
        print(f"  {date_} {t_}  {ticker} {name}  | {subj[:90]}")

    # Watchlist coverage: which of our 51 DID file a revenue announcement?
    wl_covered = sorted(watchlist & tickers_with_revenue)
    wl_missing = sorted(watchlist - tickers_with_revenue)
    print(f"\nOur watchlist tickers that filed revenue announcement in this window: {len(wl_covered)} / 51")
    print(f"  covered: {wl_covered}")
    print(f"\nOur watchlist tickers that DID NOT file via material info in this window: {len(wl_missing)}")
    print(f"  missing: {wl_missing}")


if __name__ == "__main__":
    main()
