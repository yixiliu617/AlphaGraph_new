"""
TWSE day-trading statistics scraper.

Source: https://www.twse.com.tw/exchangeReport/TWTB4U?response=json&date=YYYYMMDD
        (the page at /zh/page/trading/exchange/TWTB4U.html reads the same JSON)

Two tables per trading day, written to two parquets:

1. backend/data/taiwan/day_trading/summary.parquet
   One row per trading day -- the market-wide aggregate that powers the
   chart on the page (day-trading volume + % of total market).

   Columns:
     date (YYYY-MM-DD)
     total_shares               -- day-trading total shares traded
     total_shares_pct            -- % of market total shares
     total_buy_value_twd         -- day-trading total buy value
     total_buy_value_pct          -- % of market
     total_sell_value_twd        -- day-trading total sell value
     total_sell_value_pct         -- % of market
     scraped_at                   -- ISO timestamp (UTC)

2. backend/data/taiwan/day_trading/detail.parquet
   One row per (date, ticker) -- every security with day-trading activity.

   Columns:
     date (YYYY-MM-DD)
     ticker                -- "2330", "00400A", etc.
     name                  -- 證券名稱 in zh-TW
     suspension_flag       -- 暫停現股賣出後現款買進當沖註記 ("" when none)
     shares                -- 當日沖銷交易成交股數
     buy_value_twd         -- 當日沖銷交易買進成交金額
     sell_value_twd        -- 當日沖銷交易賣出成交金額
     scraped_at

Historical depth: endpoint serves data back to 2014-01-06 ("資訊自民國103年
1月6日起開始提供"). Non-trading days return empty tables with stat=OK.

Usage:
    # Scrape today (or the latest business day if today has no data yet)
    python tools/web_scraper/twse_day_trading.py scrape

    # Scrape one specific date
    python tools/web_scraper/twse_day_trading.py scrape --date 2026-04-24

    # Backfill all trading days since 2014-01-06 (skip weekends / holidays).
    # Takes ~100 minutes at the default 2s/request rate.
    python tools/web_scraper/twse_day_trading.py backfill

    # Backfill a narrower window
    python tools/web_scraper/twse_day_trading.py backfill --from 2024-01-01 --to 2024-12-31

    # Inspect one day's summary without writing anything
    python tools/web_scraper/twse_day_trading.py show --date 2026-04-24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import urllib3

# Python 3.13's stricter TLS validator rejects TWSE's cert chain (missing
# Subject Key Identifier). Same issue documented in the taiwan-monthly-data-
# extraction skill under CC-T1. Existing twse_historical.py uses verify=False;
# we do the same here. Safe because the payload is public open-data, the
# JSON structure is self-validating, and raise_for_status + json.loads
# provide integrity checks.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if sys.platform == "win32":
    # Chinese names in stdout -- avoid cp1252 crashes when run from a
    # non-UTF-8 console (Task Scheduler, some Git-for-Windows sessions).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR       = Path("backend/data/taiwan/day_trading")
SUMMARY_PATH   = DATA_DIR / "summary.parquet"
DETAIL_PATH    = DATA_DIR / "detail.parquet"

ENDPOINT       = "https://www.twse.com.tw/exchangeReport/TWTB4U"   # daily: 1 summary row + per-ticker detail
MONTH_ENDPOINT = "https://www.twse.com.tw/exchangeReport/TWTB4U2"  # monthly batch: ~20 daily summary rows in one call
# The daily endpoint accepts selectType filters (ETF, warrants, sector codes, etc.)
# "All" returns every security -- the only sensible choice for an archive.
SELECT_TYPE = "All"
# User-Agent: TWSE isn't picky but we send a browserlike header just in case.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.twse.com.tw/zh/page/trading/exchange/TWTB4U.html",
}

# TWSE market holidays are not programmatic; we skip weekends and any day
# that returns stat!=OK or an empty detail table. That handles all holidays
# implicitly -- the API just returns no data.
EARLIEST_DATE = date(2014, 1, 6)
REQUEST_DELAY_SEC = 2.0


def _parse_int(txt: str) -> int | None:
    txt = (txt or "").replace(",", "").strip()
    if not txt or txt in ("-", "N/A"):
        return None
    try:
        return int(txt)
    except ValueError:
        try:
            return int(float(txt))
        except ValueError:
            return None


def _parse_float(txt: str) -> float | None:
    txt = (txt or "").replace(",", "").strip()
    if not txt or txt in ("-", "N/A"):
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def fetch_one_day(d: date) -> dict:
    """Fetch one trading day's raw TWTB4U JSON.

    Returns the full response dict. Caller is responsible for detecting
    empty-data days (non-trading days) via the tables[1]['data'] length.
    """
    url = f"{ENDPOINT}?response=json&date={d.strftime('%Y%m%d')}&selectType={SELECT_TYPE}"
    resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    return resp.json()


def fetch_one_month(year: int, month: int) -> dict:
    """Fetch one month's daily-summary batch via TWTB4U2.

    Returns up to ~22 rows (one per trading day in the month). This is the
    efficient path for historical backfill: 144 calls for 12 years vs
    ~3,000 individual day calls. Response shape:
        tables[0].data = [[roc_date, shares, shares_pct, buy, buy_pct, sell, sell_pct], ...]
    `stat` is "OK" when data is returned; error strings like
    "查詢日期小於103年1月6日" (before the earliest) or
    "查詢日期大於今日" (future) signal an unusable response.
    """
    # TWTB4U2 takes a YYYYMM01 date stamp -- any day inside the month works,
    # but we send the 1st for consistency.
    yyyymmdd = f"{year:04d}{month:02d}01"
    url = f"{MONTH_ENDPOINT}?response=json&date={yyyymmdd}"
    resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    return resp.json()


def _roc_date_to_iso(s: str) -> str | None:
    """Convert "115/04/01" -> "2026-04-01". Returns None on parse failure."""
    try:
        parts = s.strip().split("/")
        if len(parts) != 3:
            return None
        roc_year = int(parts[0])
        month = int(parts[1])
        day   = int(parts[2])
        return f"{roc_year + 1911:04d}-{month:02d}-{day:02d}"
    except (ValueError, AttributeError):
        return None


def parse_month_response(payload: dict) -> list[dict]:
    """Parse TWTB4U2 monthly payload -> list of summary rows (one per trading day)."""
    stat = payload.get("stat", "")
    if stat != "OK":
        # Out-of-range queries return a human-readable Chinese error string.
        return []
    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return []

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for r in tables[0]["data"]:
        if not r or len(r) < 7:
            continue
        iso_date = _roc_date_to_iso(r[0])
        if not iso_date:
            continue
        rows.append({
            "date":                  iso_date,
            "total_shares":          _parse_int(r[1]),
            "total_shares_pct":      _parse_float(r[2]),
            "total_buy_value_twd":   _parse_int(r[3]),
            "total_buy_value_pct":   _parse_float(r[4]),
            "total_sell_value_twd":  _parse_int(r[5]),
            "total_sell_value_pct":  _parse_float(r[6]),
            "scraped_at":            now_iso,
        })
    return rows


def parse_response(d: date, payload: dict) -> tuple[dict | None, list[dict]]:
    """Parse one TWTB4U payload into (summary_row, detail_rows)."""
    if payload.get("stat") != "OK":
        return None, []
    tables = payload.get("tables") or []
    if len(tables) < 2:
        return None, []

    now_iso = datetime.now(timezone.utc).isoformat()
    date_str = d.strftime("%Y-%m-%d")

    summary_row: dict | None = None
    summary_tbl = tables[0]
    if summary_tbl.get("data"):
        r = summary_tbl["data"][0]
        # fields order (as returned by the API):
        #   當日沖銷交易總成交股數
        #   當日沖銷交易總成交股數占市場比重%
        #   當日沖銷交易總買進成交金額
        #   當日沖銷交易總買進成交金額占市場比重%
        #   當日沖銷交易總賣出成交金額
        #   當日沖銷交易總賣出成交金額占市場比重%
        summary_row = {
            "date":                        date_str,
            "total_shares":                _parse_int(r[0]),
            "total_shares_pct":            _parse_float(r[1]),
            "total_buy_value_twd":         _parse_int(r[2]),
            "total_buy_value_pct":         _parse_float(r[3]),
            "total_sell_value_twd":        _parse_int(r[4]),
            "total_sell_value_pct":        _parse_float(r[5]),
            "scraped_at":                  now_iso,
        }

    detail_rows: list[dict] = []
    detail_tbl = tables[1]
    for r in detail_tbl.get("data", []):
        # fields order:
        #   證券代號, 證券名稱, 暫停現股賣出後現款買進當沖註記,
        #   當日沖銷交易成交股數, 當日沖銷交易買進成交金額,
        #   當日沖銷交易賣出成交金額
        if not r or not r[0]:
            continue
        detail_rows.append({
            "date":            date_str,
            "ticker":          str(r[0]).strip(),
            "name":            (r[1] or "").strip(),
            "suspension_flag": (r[2] or "").strip(),
            "shares":          _parse_int(r[3]),
            "buy_value_twd":   _parse_int(r[4]),
            "sell_value_twd":  _parse_int(r[5]),
            "scraped_at":      now_iso,
        })
    return summary_row, detail_rows


def upsert(
    summary_rows: list[dict],
    detail_rows: list[dict],
) -> tuple[int, int]:
    """Merge new rows into the two parquets, de-duping on the key columns.
    Returns (new_summary_count, new_detail_count)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    new_summary = 0
    if summary_rows:
        new_sdf = pd.DataFrame(summary_rows)
        if SUMMARY_PATH.exists():
            existing = pd.read_parquet(SUMMARY_PATH)
            merged = pd.concat([existing, new_sdf], ignore_index=True)
            before = len(existing)
            merged = merged.drop_duplicates(subset=["date"], keep="last")
            new_summary = len(merged) - before
        else:
            merged = new_sdf
            new_summary = len(merged)
        merged = merged.sort_values("date").reset_index(drop=True)
        merged.to_parquet(SUMMARY_PATH, index=False, compression="zstd")

    new_detail = 0
    if detail_rows:
        new_ddf = pd.DataFrame(detail_rows)
        if DETAIL_PATH.exists():
            existing = pd.read_parquet(DETAIL_PATH)
            merged = pd.concat([existing, new_ddf], ignore_index=True)
            before = len(existing)
            merged = merged.drop_duplicates(subset=["date", "ticker"], keep="last")
            new_detail = len(merged) - before
        else:
            merged = new_ddf
            new_detail = len(merged)
        merged = merged.sort_values(["date", "ticker"]).reset_index(drop=True)
        merged.to_parquet(DETAIL_PATH, index=False, compression="zstd")

    return new_summary, new_detail


def daterange(start: date, end: date):
    d = start
    while d <= end:
        # Skip weekends up-front (TWSE is closed Sat/Sun).
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def monthrange(start: date, end: date):
    """Yield (year, month) pairs inclusive of start/end months."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


def cmd_scrape(args) -> int:
    d = _parse_date(args.date) if args.date else date.today()
    print(f">> fetching TWTB4U date={d}")
    payload = fetch_one_day(d)
    summary, detail = parse_response(d, payload)
    if summary is None or not detail:
        print(f"   (no data; likely non-trading day or early morning pre-publish)")
        return 0
    ns, nd = upsert([summary], detail)
    print(f"   OK  summary: +{ns}  detail: +{nd} rows")
    print(f"   day-trading {summary['total_shares_pct']:.2f}% of total market shares "
          f"({summary['total_shares']:,})")
    return 0


def cmd_show(args) -> int:
    d = _parse_date(args.date)
    print(f">> fetching TWTB4U date={d}")
    payload = fetch_one_day(d)
    summary, detail = parse_response(d, payload)
    if summary is None:
        print(f"   stat={payload.get('stat')} -- no usable data for {d}")
        return 0
    print()
    print("  --- summary ---")
    for k, v in summary.items():
        print(f"    {k:<30} {v}")
    print()
    print(f"  --- top 15 day-trading tickers by shares (of {len(detail)} total) ---")
    top = sorted(detail, key=lambda r: (r["shares"] or 0), reverse=True)[:15]
    for r in top:
        print(f"    {r['ticker']:<8} {r['name'][:18]:<18} "
              f"shares={r['shares']:>15,}  buy_twd={r['buy_value_twd']:>15,}")
    return 0


def cmd_backfill(args) -> int:
    """Monthly-batch backfill via TWTB4U2.

    ~20x fewer requests than per-day, so a full 2014-01 -> today run takes
    ~5 minutes instead of ~100. Populates only the summary parquet --
    per-ticker detail stays with the per-day `scrape` path because TWTB4U2
    does not carry it.
    """
    start = _parse_date(args.start) if args.start else EARLIEST_DATE
    end   = _parse_date(args.end)   if args.end   else date.today()
    if start < EARLIEST_DATE:
        print(f"  clamping start to EARLIEST_DATE={EARLIEST_DATE}")
        start = EARLIEST_DATE

    # Resume by skipping months whose every trading day is already in the
    # summary parquet. We re-fetch the current month even when partially
    # covered, because new trading days land as the month progresses.
    already_done_months: set[tuple[int, int]] = set()
    if SUMMARY_PATH.exists():
        existing = pd.read_parquet(SUMMARY_PATH, columns=["date"])["date"].astype(str)
        # "2024-07" occurrence -> we only consider the month fully-done
        # when it's strictly before the current month; the current month
        # always gets re-fetched to pick up new days.
        today_ym = (date.today().year, date.today().month)
        for ym in existing.str.slice(0, 7).unique():
            y, m = int(ym[:4]), int(ym[5:7])
            if (y, m) < today_ym:
                # Heuristic: any month before current with at least one
                # row is treated as complete. Rerun with --force to rebuild.
                already_done_months.add((y, m))
        if already_done_months:
            print(f">> resuming: {len(already_done_months)} past months already covered")

    months = [(y, m) for (y, m) in monthrange(start, end)
              if args.force or (y, m) not in already_done_months]
    print(f">> backfilling {len(months)} months  [{start.isoformat()[:7]} -> {end.isoformat()[:7]}]  delay={REQUEST_DELAY_SEC}s")
    if not months:
        print("   nothing to do.")
        return 0

    batch: list[dict] = []
    FLUSH_EVERY = 12   # flush once a calendar year's worth of months

    total_ok = 0
    total_empty = 0
    total_err = 0

    for i, (y, m) in enumerate(months):
        try:
            payload = fetch_one_month(y, m)
            rows = parse_month_response(payload)
            if not rows:
                total_empty += 1
                stat = payload.get("stat", "?")
                print(f"  [{i+1}/{len(months)}] {y}-{m:02d} empty  stat={stat[:60]}")
            else:
                batch.extend(rows)
                total_ok += 1
                # Quick preview of first/last day of the month.
                first_pct = rows[0]["total_shares_pct"]
                last_pct  = rows[-1]["total_shares_pct"]
                print(f"  [{i+1}/{len(months)}] {y}-{m:02d} OK  "
                      f"{len(rows):>2} days  shares%={first_pct:>5.2f}->{last_pct:>5.2f}")
        except Exception as exc:
            total_err += 1
            print(f"  [{i+1}/{len(months)}] {y}-{m:02d} ERR {exc}")

        if (i + 1) % FLUSH_EVERY == 0:
            ns, _ = upsert(batch, [])
            print(f"     flush: +{ns} summary rows")
            batch.clear()

        if i < len(months) - 1:
            time.sleep(REQUEST_DELAY_SEC)

    if batch:
        ns, _ = upsert(batch, [])
        print(f">> final flush: +{ns} summary rows")

    print(f">> done. ok={total_ok} empty={total_empty} err={total_err}")
    return 0


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description="TWSE day-trading statistics scraper (TWTB4U)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape", help="Scrape one day (today by default)")
    sp.add_argument("--date", help="YYYY-MM-DD (default: today)")
    sp.set_defaults(func=cmd_scrape)

    sp = sub.add_parser("show", help="Print one day without saving")
    sp.add_argument("--date", required=True, help="YYYY-MM-DD")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("backfill", help="Monthly-batch backfill via TWTB4U2 (~5 min for 12 years)")
    sp.add_argument("--from",  dest="start", help="YYYY-MM-DD (default: 2014-01-06)")
    sp.add_argument("--to",    dest="end",   help="YYYY-MM-DD (default: today)")
    sp.add_argument("--force", action="store_true", help="Re-fetch months already in summary.parquet")
    sp.set_defaults(func=cmd_backfill)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
