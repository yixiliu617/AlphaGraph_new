"""
TWSE 三大法人 buy/sell flow scraper (BFI82U).

Source: https://www.twse.com.tw/zh/trading/foreign/bfi82u.html
        Underlying JSON: https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate=YYYYMMDD&type=day&response=json

Captures the daily buy/sell/net values for the three institutional-investor
groups that TWSE tracks (collectively 三大法人):

  proprietary trading desks       (自營商)
  domestic mutual funds           (投信)
  foreign + mainland-China money  (外資及陸資)

The schema evolved through four eras (confirmed by probing live):

  2004-06 -> ~2008 :  4 rows -- 自營商 + 投信 + 外資 + 合計
                                 (pre-2009: foreign-only, no China money)
  ~2009 -> 2014    :  4 rows -- 自營商 + 投信 + 外資及陸資 + 合計
                                 (China money added to foreign category)
  2014-12 -> 2019  :  5 rows -- 自營商 split into self-trade vs. hedge
  2020+            :  6 rows -- 外資 also split into 外資及陸資(非自營) vs. 外資自營商

Storage uses LONG FORMAT (one row per (date, investor_type)) so the
schema evolution is non-destructive: older rows just don't have the
finer-grained investor categories.

  backend/data/taiwan/foreign_flow/data.parquet

  date              YYYY-MM-DD
  investor_type     canonical key: foreign | foreign_prop | foreign_legacy
                                   prop_self | prop_hedge | prop_legacy
                                   trust | total
  buy_value_twd     int (raw TWD)
  sell_value_twd    int
  net_buy_twd       int (positive = net inflow)
  scraped_at        ISO UTC

CLI usage:
    python tools/web_scraper/twse_foreign_flow.py scrape                    # today
    python tools/web_scraper/twse_foreign_flow.py scrape --date 2026-04-24
    python tools/web_scraper/twse_foreign_flow.py show   --date 2026-04-24
    python tools/web_scraper/twse_foreign_flow.py backfill                  # 2004-06-01 -> today
    python tools/web_scraper/twse_foreign_flow.py backfill --from 2024-01-01

Note: TWSE has a monthly batch endpoint (type=month) but it's been returning
"網站維護中" (maintenance) since at least 2026-04-25. We fall back to per-day
calls. Backfill of 22 years -> ~5,400 trading days @ 2 s/req = ~3 hours.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import urllib3

# Same Python 3.13 + TWSE TLS workaround as twse_day_trading.py.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR  = Path("backend/data/taiwan/foreign_flow")
DATA_PATH = DATA_DIR / "data.parquet"

ENDPOINT = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.twse.com.tw/zh/trading/foreign/bfi82u.html",
}

EARLIEST_DATE = date(2004, 6, 1)   # confirmed via probing (Apr-May 2004 returned no data)
REQUEST_DELAY_SEC = 2.0

# Map TWSE's Chinese investor labels to stable English keys. Four eras of
# label conventions are covered (see module docstring for date ranges). For
# UI continuity, the panel can stitch together a single 22-year "foreign net
# buy" line by coalescing  foreign / foreign_legacy / foreign_only  in date
# order -- only one of those keys has data on any given trading day.
_INVESTOR_MAP = {
    # ---- proprietary trading desk(s) ----
    "自營商":                  "prop_legacy",   # pre-2014, single line
    "自營商(自行買賣)":         "prop_self",     # 2014-12 onwards
    "自營商(避險)":             "prop_hedge",    # 2014-12 onwards
    # ---- domestic mutual funds ----
    "投信":                    "trust",         # all eras
    # ---- foreign + mainland-China money ----
    "外資":                    "foreign_only",  # 2004-06 to ~2008 (no China money)
    "外資及陸資":              "foreign_legacy",# ~2009 to 2019 (China added, no foreign-prop split)
    "外資及陸資(不含外資自營商)": "foreign",       # 2020+ (post foreign-prop split)
    "外資自營商":              "foreign_prop",  # 2020+ only
    # ---- aggregate ----
    "合計":                    "total",
}


def _parse_int(s: str) -> int | None:
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def fetch_one_day(d: date) -> dict:
    """Fetch one trading day's BFI82U JSON. Caller checks `stat == "OK"`."""
    url = f"{ENDPOINT}?dayDate={d.strftime('%Y%m%d')}&type=day&response=json"
    resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    return resp.json()


def parse_response(d: date, payload: dict) -> list[dict]:
    """Parse BFI82U payload -> list of rows (one per investor_type).
    Returns [] if non-trading day or if the response is otherwise empty.
    """
    if payload.get("stat") != "OK":
        return []
    data = payload.get("data") or []
    if not data:
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    date_str = d.strftime("%Y-%m-%d")
    rows: list[dict] = []
    seen_unmapped: set[str] = set()
    for r in data:
        if not r or len(r) < 4:
            continue
        label = (r[0] or "").strip()
        # Strip combining whitespace / trailing punct.
        normalized = label.replace(" ", "").replace("　", "")
        investor = _INVESTOR_MAP.get(normalized)
        if investor is None:
            # New TWSE label we haven't seen -- log once and skip rather
            # than corrupt the parquet with raw zh labels mixed with
            # English keys.
            if normalized not in seen_unmapped:
                print(f"   WARN: unmapped investor label {normalized!r} -- skipping")
                seen_unmapped.add(normalized)
            continue
        rows.append({
            "date":            date_str,
            "investor_type":   investor,
            "buy_value_twd":   _parse_int(r[1]),
            "sell_value_twd":  _parse_int(r[2]),
            "net_buy_twd":     _parse_int(r[3]),
            "scraped_at":      now_iso,
        })
    return rows


def upsert(rows: list[dict]) -> int:
    """Replace per (date, investor_type) — returns count of rows after merge."""
    if not rows:
        return 0
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if DATA_PATH.exists():
        existing = pd.read_parquet(DATA_PATH)
        merged = pd.concat([existing, new_df], ignore_index=True)
    else:
        merged = new_df
    merged = merged.drop_duplicates(subset=["date", "investor_type"], keep="last")
    merged = merged.sort_values(["date", "investor_type"]).reset_index(drop=True)
    merged.to_parquet(DATA_PATH, index=False, compression="zstd")
    return len(merged)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:    # skip Sat/Sun up front; holidays handled by empty-data check
            yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_scrape(args) -> int:
    d = _parse_date(args.date) if args.date else date.today()
    print(f">> fetching BFI82U date={d}")
    payload = fetch_one_day(d)
    rows = parse_response(d, payload)
    if not rows:
        print(f"   (no data; likely non-trading day. stat={payload.get('stat','?')!r})")
        return 0
    n = upsert(rows)
    print(f"   OK  inserted/updated {len(rows)} rows  (total in parquet: {n})")
    foreign = next((r for r in rows if r["investor_type"] == "foreign"), None)
    total   = next((r for r in rows if r["investor_type"] == "total"),   None)
    if foreign:
        sign = "+" if (foreign["net_buy_twd"] or 0) >= 0 else ""
        print(f"   foreign net buy: {sign}NT${(foreign['net_buy_twd'] or 0)/1e9:.2f}B")
    if total:
        sign = "+" if (total["net_buy_twd"] or 0) >= 0 else ""
        print(f"   total net buy:   {sign}NT${(total['net_buy_twd'] or 0)/1e9:.2f}B")
    return 0


def cmd_show(args) -> int:
    d = _parse_date(args.date)
    print(f">> fetching BFI82U date={d}")
    payload = fetch_one_day(d)
    rows = parse_response(d, payload)
    if not rows:
        print(f"   stat={payload.get('stat','?')} -- no data")
        return 0
    print()
    print(f"  {'investor_type':<18} {'buy (NT$B)':>14} {'sell (NT$B)':>14} {'net (NT$B)':>14}")
    for r in rows:
        b = (r['buy_value_twd']  or 0) / 1e9
        s = (r['sell_value_twd'] or 0) / 1e9
        n = (r['net_buy_twd']    or 0) / 1e9
        print(f"  {r['investor_type']:<18} {b:>14.2f} {s:>14.2f} {n:>+14.2f}")
    return 0


def cmd_backfill(args) -> int:
    start = _parse_date(args.start) if args.start else EARLIEST_DATE
    end   = _parse_date(args.end)   if args.end   else date.today()
    if start < EARLIEST_DATE:
        print(f"  clamping start to EARLIEST_DATE={EARLIEST_DATE}")
        start = EARLIEST_DATE

    # Resume: skip dates whose `total` row is already in the parquet.
    already_done: set[str] = set()
    if DATA_PATH.exists() and not args.force:
        existing = pd.read_parquet(DATA_PATH, columns=["date", "investor_type"])
        already_done = set(
            existing[existing["investor_type"] == "total"]["date"].astype(str)
        )
        print(f">> resuming: {len(already_done)} days already in parquet")

    days = [d for d in daterange(start, end) if d.strftime("%Y-%m-%d") not in already_done]
    print(f">> backfilling {len(days)} business days  [{start} -> {end}]  delay={REQUEST_DELAY_SEC}s")
    if not days:
        print("   nothing to do.")
        return 0

    batch: list[dict] = []
    FLUSH_EVERY = 100   # flush every ~5 weeks of trading days

    total_ok = 0
    total_empty = 0
    total_err = 0

    for i, d in enumerate(days):
        try:
            payload = fetch_one_day(d)
            rows = parse_response(d, payload)
            if not rows:
                total_empty += 1
                if (i + 1) % 50 == 0:    # print every 50th to keep log compact
                    print(f"  [{i+1}/{len(days)}] {d} empty (holiday)")
            else:
                batch.extend(rows)
                total_ok += 1
                if (i + 1) % 50 == 0:
                    foreign = next((r for r in rows if r["investor_type"] == "foreign"), None)
                    fnb = (foreign["net_buy_twd"] / 1e9) if foreign and foreign["net_buy_twd"] else 0.0
                    print(f"  [{i+1}/{len(days)}] {d} OK  foreign net {fnb:+.2f}B")
        except Exception as exc:
            total_err += 1
            print(f"  [{i+1}/{len(days)}] {d} ERR {exc}")

        if (i + 1) % FLUSH_EVERY == 0:
            n = upsert(batch)
            print(f"     flush after {i+1} days: parquet now {n} rows")
            batch.clear()

        if i < len(days) - 1:
            time.sleep(REQUEST_DELAY_SEC)

    if batch:
        n = upsert(batch)
        print(f">> final flush: parquet now {n} rows")

    print(f">> done. ok={total_ok} empty={total_empty} err={total_err}")
    return 0


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description="TWSE 三大法人 BFI82U scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape", help="Scrape one day (today by default)")
    sp.add_argument("--date", help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_scrape)

    sp = sub.add_parser("show", help="Print one day without saving")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("backfill", help="Per-day backfill (slow ~3h for 22yrs)")
    sp.add_argument("--from",  dest="start", help="YYYY-MM-DD (default: 2004-06-01)")
    sp.add_argument("--to",    dest="end",   help="YYYY-MM-DD (default: today)")
    sp.add_argument("--force", action="store_true",
                    help="Re-fetch even days already in parquet")
    sp.set_defaults(func=cmd_backfill)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
