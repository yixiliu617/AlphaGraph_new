"""
NASDAQ earnings calendar scraper.

Fetches the upcoming earnings calendar from NASDAQ's public JSON API:
    GET https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD

NASDAQ returns ALL US-listed names scheduled for a given calendar date:
ticker, company name, time-of-day code (BMO / AMC / blank), fiscal quarter
ending, EPS forecast, last-year report date. We only care about ticker +
date + time-of-day to populate the earnings calendar's "upcoming" rows.

Per the project-wide cache-first rule (CLAUDE.md), the raw NASDAQ JSON is
persisted on every fetch:
    backend/data/_raw/nasdaq_earnings_calendar/YYYY-MM-DD.json

Downstream consumers read from the silver parquet, never re-hit NASDAQ.

Usage (programmatic — used by backend/scripts/refresh_calendar_us.py):
    from tools.web_scraper.nasdaq_earnings_calendar import fetch_range
    rows = fetch_range(start_date, end_date)   # canonical event dicts

CLI (manual fetch):
    python tools/web_scraper/nasdaq_earnings_calendar.py fetch --days 21
    python tools/web_scraper/nasdaq_earnings_calendar.py fetch --date 2026-05-01

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET  = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _PROJECT_ROOT / "backend" / "data" / "_raw" / "nasdaq_earnings_calendar"

_API = "https://api.nasdaq.com/api/calendar/earnings"
# NASDAQ's WAF rejects requests without a real-browser User-Agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

# Map NASDAQ's "time" code to a (hour, minute) tuple in America/New_York.
# BMO = Before Market Open: companies typically release ~07:00-08:30 ET.
# AMC = After Market Close: ~16:05-17:00 ET.
# We pick representative midpoints; LLM enrichment can refine to the real
# call time when we add Layer 2.
_TIME_OF_DAY_ET: dict[str, tuple[int, int] | None] = {
    "time-pre-market":  (8, 0),    # BMO: 08:00 ET
    "time-after-hours": (16, 30),  # AMC: 16:30 ET
    "":                 None,      # unspecified
}

# Friendly time-of-day code shown in the UI badge.
_TIME_OF_DAY_LABEL: dict[str, str] = {
    "time-pre-market":  "BMO",
    "time-after-hours": "AMC",
    "":                 "TBD",
}


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------

def _parse_money(s: str | None) -> float | None:
    """Parse strings like '$3,500,000,000,000' or '$2.81' to a float USD value.
    Returns None if parsing fails or the string is empty / 'N/A'."""
    if not s:
        return None
    cleaned = re.sub(r"[$,\s]", "", s)
    if not cleaned or cleaned.upper() in ("N/A", "NA", "-"):
        return None
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _parse_int(s: str | None) -> int | None:
    """Parse '13' or '13 ' to int; None if invalid."""
    if not s:
        return None
    cleaned = re.sub(r"[,\s]", "", s)
    if not cleaned or cleaned.upper() in ("N/A", "NA", "-"):
        return None
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return None


def _parse_date_us(s: str | None) -> str | None:
    """Parse NASDAQ date strings like '4/24/2025', 'Apr 24, 2025' to
    'YYYY-MM-DD'. Returns None if parsing fails."""
    if not s:
        return None
    s = s.strip()
    if not s or s.upper() in ("N/A", "NA", "-"):
        return None
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_one_day(date: str, *, retries: int = 3, sleep_between: float = 1.0) -> dict | None:
    """Hit NASDAQ's calendar API for one date. Returns parsed JSON or None.
    Persists raw bytes to bronze on success."""
    url = f"{_API}?date={date}"
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers=_HEADERS)
            with urlopen(req, timeout=15) as resp:
                raw = resp.read()
        except (URLError, HTTPError, TimeoutError) as e:
            last_err = e
            time.sleep(sleep_between * (attempt + 1))
            continue

        # Bronze persist (cache-first rule).
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _RAW_DIR / f"{date}.json"
        try:
            out_path.write_bytes(raw)
        except Exception as exc:
            logger.warning("[%s] failed to write bronze cache: %s", date, exc)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = e
            time.sleep(sleep_between * (attempt + 1))
            continue

    logger.warning("[%s] fetch failed after %d retries: %s", date, retries, last_err)
    return None


def _parse_day_payload(date: str, payload: dict | None) -> list[dict]:
    """Convert one day's NASDAQ payload to canonical event dicts.

    Returns rows with these fields (the calendar-storage schema):
        ticker, market, fiscal_period, release_datetime_utc, release_local_tz,
        status, source, source_id

    Soft-data fields (press_release_url, webcast_url, etc.) are left empty;
    the orchestrator populates them later via 8-K cross-reference or LLM.
    """
    if not payload or "data" not in payload or payload.get("status", {}).get("rCode") != 200:
        return []
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    if not rows:
        return []

    # Convert announcement date to datetime at the time-of-day midpoint.
    try:
        date_dt = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("invalid date string: %s", date)
        return []

    out: list[dict] = []
    for r in rows:
        symbol = (r.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        time_code = (r.get("time") or "").strip()
        tod = _TIME_OF_DAY_ET.get(time_code)

        # DST-aware ET -> UTC. zoneinfo handles the EDT (-4) / EST (-5)
        # transition automatically based on the actual date. We KEEP tzinfo
        # on the returned datetime because the parquet column is typed as
        # datetime64[us, UTC] and pandas refuses naive assignments.
        if tod is not None:
            hour, minute = tod
            local_aware = datetime(
                date_dt.year, date_dt.month, date_dt.day, hour, minute,
                tzinfo=_ET,
            )
            release_utc = local_aware.astimezone(_UTC)
        else:
            # Date only: midnight UTC, tz-aware.
            release_utc = datetime(
                date_dt.year, date_dt.month, date_dt.day, 0, 0, tzinfo=_UTC,
            )

        # Fiscal period from "Mar/2026" -> "FY2026-Q?" requires per-ticker
        # fiscal-calendar knowledge we don't have here. Use a date stamp;
        # the orchestrator can refine later by joining against the topline
        # parquet's fiscal_map.
        fq = (r.get("fiscalQuarterEnding") or "").strip()
        fiscal_period = _fiscal_period_from_nasdaq_fq(fq) if fq else f"AS-OF-{date}"

        out.append({
            "ticker":               symbol,
            "market":               "US",
            "fiscal_period":        fiscal_period,
            "release_datetime_utc": release_utc,
            "release_local_tz":     "America/New_York",
            "status":               "upcoming",
            "press_release_url":    None,
            "filing_url":           None,
            "webcast_url":          None,
            "transcript_url":       None,
            "dial_in_phone":        None,
            "dial_in_pin":          None,
            "source":               "nasdaq_calendar",
            "source_id":            f"nasdaq:{date}:{symbol}",
            # Friendly time-of-day code (BMO/AMC/TBD) — preserved through the
            # parquet so the UI can show a tz-independent badge.
            "time_of_day_code":     _TIME_OF_DAY_LABEL.get(time_code, "TBD"),
            # NASDAQ-rich estimate fields. None when missing.
            "eps_forecast":         _parse_money(r.get("epsForecast")),
            "eps_estimates_count":  _parse_int(r.get("noOfEsts")),
            "market_cap":           _parse_money(r.get("marketCap")),
            "last_year_eps":        _parse_money(r.get("lastYearEPS")),
            "last_year_report_date": _parse_date_us(r.get("lastYearRptDt")),
        })
    return out


_MONTH_TO_QUARTER = {
    # NASDAQ reports the LAST month of the fiscal quarter ending. For a
    # company with a calendar fiscal year these line up; for non-calendar
    # filers (NVDA, AAPL, MSFT) they don't, so we still treat this as a
    # rough/synthetic label and let the orchestrator override.
    "Mar": ("Q1", lambda y: y),
    "Jun": ("Q2", lambda y: y),
    "Sep": ("Q3", lambda y: y),
    "Dec": ("Q4", lambda y: y),
}


def _fiscal_period_from_nasdaq_fq(fq: str) -> str:
    """Convert "Mar/2026" -> "FY2026-Q1" using calendar-year mapping.
    Non-calendar filers (NVDA, MSFT, etc.) get refined by the orchestrator."""
    parts = fq.split("/")
    if len(parts) != 2:
        return f"AS-OF-{fq}"
    mon, year = parts[0].strip(), parts[1].strip()
    try:
        year_i = int(year)
    except ValueError:
        return f"AS-OF-{fq}"
    info = _MONTH_TO_QUARTER.get(mon)
    if not info:
        return f"AS-OF-{fq}"
    quarter, _yfn = info
    return f"FY{year_i}-{quarter}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_range(
    start_date: datetime, end_date: datetime, *,
    sleep_between: float = 0.4,
    use_cache: bool = True,
) -> list[dict]:
    """Fetch NASDAQ earnings calendar for every weekday in [start_date, end_date].

    Returns canonical event dicts ready for upsert to events.parquet.

    `use_cache=True`: if a bronze JSON for a date is already on disk, parse
    that instead of re-hitting NASDAQ. Set False to force-refresh.
    """
    out: list[dict] = []
    cur = start_date
    while cur.date() <= end_date.date():
        # Skip weekends -- NASDAQ doesn't schedule earnings on Sat/Sun.
        if cur.weekday() < 5:
            date_str = cur.strftime("%Y-%m-%d")
            payload: dict | None = None
            cache_path = _RAW_DIR / f"{date_str}.json"
            if use_cache and cache_path.exists():
                try:
                    payload = json.loads(cache_path.read_bytes())
                    logger.debug("[%s] cache hit", date_str)
                except Exception:
                    payload = None
            if payload is None:
                payload = _fetch_one_day(date_str)
                time.sleep(sleep_between)
            rows = _parse_day_payload(date_str, payload)
            logger.info("[%s] %d events", date_str, len(rows))
            out.extend(rows)
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    p = argparse.ArgumentParser(description="NASDAQ earnings calendar scraper")
    sub = p.add_subparsers(dest="cmd", required=True)

    fp = sub.add_parser("fetch", help="Fetch NASDAQ calendar JSON")
    fp.add_argument("--days", type=int, default=21,
                    help="Days forward to fetch (default 21)")
    fp.add_argument("--date", type=str, default=None,
                    help="Single date YYYY-MM-DD; overrides --days")
    fp.add_argument("--no-cache", action="store_true",
                    help="Force re-fetch (ignore bronze cache)")

    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.cmd == "fetch":
        if args.date:
            start = end = datetime.strptime(args.date, "%Y-%m-%d")
        else:
            start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=args.days)
        rows = fetch_range(start, end, use_cache=not args.no_cache)
        print(f"[nasdaq] total events fetched: {len(rows)}")
        # Sample
        for r in rows[:5]:
            print(f"  {r['ticker']:<8} {r['fiscal_period']:<14} "
                  f"{r['release_datetime_utc']}  {r['_nasdaq_time_code']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
