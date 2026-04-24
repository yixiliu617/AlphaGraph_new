"""
MOPS material-information (重大訊息 / t05st02) supplemental scraper.

Measured coverage (see .claude/skills/taiwan-monthly-data-extraction/SKILL.md)
is ~2% of our watchlist in the publication window — this is a SUPPLEMENT
to the primary monthly_revenue.py scraper, not a replacement. Use it to
catch the handful of tickers (MediaTek-class) that reliably post material
info BEFORE the structured filing surfaces in t146sb05_detail, giving a
head-start on those specific names.

Output:
  - material_info/data.parquet — dedup'd announcements for watchlist
    tickers whose subject matches a revenue keyword.
  - Returns a set of (ticker, fiscal_ym_guess) pairs that the caller can
    use to trigger an immediate monthly_revenue poll for those tickers.

Endpoint:
  POST /mops/api/t05st02   body: {"year":"115","month":"04","day":"10"}
  response.result.data rows are:
    [announcement_date, announcement_time, ticker, name_zh, subject, params]
  params = {"parameters": {...}, "apiName": "t05st01_detail"}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import list_watchlist_tickers
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    UpsertStats,
    upsert_material_info,
)

logger = logging.getLogger(__name__)

TPE = ZoneInfo("Asia/Taipei")

_ENDPOINT = "/mops/api/t05st02"

# Subjects with any of these tokens → treat as monthly-revenue announcement.
# Derived from observed phrasing over an 11-day April 2026 sample.
REVENUE_KEYWORDS = ("營業額", "營業收入", "月份營收", "自結", "合併營收")

# Non-revenue false positives that would sneak past REVENUE_KEYWORDS if we
# only used the positive list. 自結 shows up on many things (負債比率、
# 流動比率、速動比率…). We require that alongside the 自結 match, at least
# one of the stronger revenue tokens appears too.
_STRONG_REVENUE_TOKENS = ("營業額", "營業收入", "月份營收", "合併營收", "月營收")
_WEAK_REVENUE_TOKENS = ("自結",)

# Subjects use inconsistent year conventions — some filers use ROC (民國)
# "115年3月"; others use AD "2026年3月". Pattern accepts either.
#   ROC matches 1xx (100-199), giving years 2011-2110
#   AD matches  20xx, giving years 2000-2099
_FISCAL_YM_ROC_RE = re.compile(r"(1\d{2})\s*年\s*(\d{1,2})\s*月")
_FISCAL_YM_AD_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")


def looks_like_revenue_subject(subject: str) -> bool:
    """True if the subject line is the monthly-revenue flavor.

    Passes 'strong' matches directly. Passes 'weak' matches only if a
    strong keyword is also present — this filters out 自結財務比率,
    自結損益, 自結應收帳款 etc. which share the 自結 prefix but aren't
    revenue filings.
    """
    if not subject:
        return False
    has_strong = any(k in subject for k in _STRONG_REVENUE_TOKENS)
    has_weak = any(k in subject for k in _WEAK_REVENUE_TOKENS)
    return has_strong or (has_weak and has_strong)


def extract_fiscal_ym_guess(subject: str) -> str:
    """Parse the fiscal (YYYY-MM) from the announcement subject.

    Handles both ROC ('115年03月') and AD ('2026年3月') formats — filers
    use either without a rule. Returns '' if neither matches.
    """
    if not subject:
        return ""
    m = _FISCAL_YM_ROC_RE.search(subject)
    if m:
        roc_year = int(m.group(1))
        month = int(m.group(2))
        if 1 <= month <= 12:
            return f"{roc_year + 1911:04d}-{month:02d}"
    m = _FISCAL_YM_AD_RE.search(subject)
    if m:
        ad_year = int(m.group(1))
        month = int(m.group(2))
        if 1 <= month <= 12:
            return f"{ad_year:04d}-{month:02d}"
    return ""


@dataclass
class MaterialInfoMatch:
    """One matched revenue announcement for a watchlist ticker."""
    ticker: str
    name_zh: str
    announcement_date: str       # raw e.g. '115/04/10'
    announcement_time: str       # raw e.g. '17:30:27'
    announcement_datetime: str   # normalized '2026-04-10T17:30:27'
    subject: str
    fiscal_ym_guess: str         # '2026-03' if parsable; else ''
    parameters_json: str         # raw params dict serialized

    def to_canonical(self) -> dict:
        return {
            "ticker": self.ticker,
            "name_zh": self.name_zh,
            "announcement_date": self.announcement_date,
            "announcement_time": self.announcement_time,
            "announcement_datetime": self.announcement_datetime,
            "subject": self.subject,
            "filing_type": "monthly_revenue",
            "fiscal_ym_guess": self.fiscal_ym_guess,
            "parameters_json": self.parameters_json,
        }


def _roc_dt_to_iso(date_str: str, time_str: str) -> str:
    """'115/04/10' + '17:30:27' -> '2026-04-10T17:30:27'."""
    try:
        parts = date_str.split("/")
        roc_y = int(parts[0])
        mo = int(parts[1])
        dy = int(parts[2])
        ad_y = roc_y + 1911
        return f"{ad_y:04d}-{mo:02d}-{dy:02d}T{time_str.strip()}"
    except (ValueError, IndexError):
        return f"{date_str}T{time_str}"  # best-effort pass-through


def fetch_day(
    client: MopsClient, *, roc_year: int, month: int, day: int,
) -> list[list]:
    """Return the raw result.data rows from t05st02 for one ROC date."""
    res = client.post_json(_ENDPOINT, {
        "year": str(roc_year),
        "month": f"{month:02d}",
        "day": f"{day:02d}",
    })
    if res.status_code != 200:
        logger.warning("t05st02 fetch failed %03d/%02d/%02d status=%d",
                       roc_year, month, day, res.status_code)
        return []
    try:
        body = res.json()
    except ValueError:
        logger.warning("t05st02 non-JSON body")
        return []
    if body.get("code") != 200:
        return []
    return body.get("result", {}).get("data", []) or []


def extract_revenue_matches(
    rows: list[list], *, watchlist: set[str],
) -> list[MaterialInfoMatch]:
    """Filter raw rows → revenue announcements that hit our watchlist."""
    out: list[MaterialInfoMatch] = []
    for r in rows:
        if len(r) < 5:
            continue
        ann_date = str(r[0]).strip()
        ann_time = str(r[1]).strip()
        ticker = str(r[2]).strip()
        name = str(r[3]).strip()
        subject = str(r[4]).strip()
        params = r[5] if len(r) > 5 else None

        if ticker not in watchlist:
            continue
        if not looks_like_revenue_subject(subject):
            continue

        out.append(MaterialInfoMatch(
            ticker=ticker,
            name_zh=name,
            announcement_date=ann_date,
            announcement_time=ann_time,
            announcement_datetime=_roc_dt_to_iso(ann_date, ann_time),
            subject=subject,
            fiscal_ym_guess=extract_fiscal_ym_guess(subject),
            parameters_json=json.dumps(params, ensure_ascii=False) if params is not None else "",
        ))
    return out


def scrape_material_info_window(
    client: MopsClient,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    target_days: list[date] | None = None,
) -> tuple[UpsertStats, set[tuple[str, str]]]:
    """Poll t05st02 for today + yesterday in TPE time, filter, upsert.

    Returns (stats, trigger_set) where trigger_set is {(ticker,
    fiscal_ym_guess)} for every MATCHED (watchlist-ticker × revenue
    keyword) announcement. Caller uses trigger_set to kick an immediate
    t146sb05_detail poll for those tickers instead of waiting for the
    next monthly_revenue_window tick.
    """
    from datetime import datetime as _dt
    if target_days is None:
        now_tpe = _dt.now(TPE).date()
        # Yesterday too — filings past midnight get stamped to the prior
        # trading day sometimes, and our 15-min cadence straddles midnight.
        yesterday = date.fromordinal(now_tpe.toordinal() - 1)
        target_days = [yesterday, now_tpe]

    watchlist = set(list_watchlist_tickers())
    all_matches: list[MaterialInfoMatch] = []
    for d in target_days:
        roc_year = d.year - 1911
        raw = fetch_day(client, roc_year=roc_year, month=d.month, day=d.day)
        matches = extract_revenue_matches(raw, watchlist=watchlist)
        if matches:
            logger.info("t05st02 %s: %d watchlist revenue matches",
                        d.isoformat(), len(matches))
        all_matches.extend(matches)

    if not all_matches:
        return UpsertStats(), set()

    rows = [m.to_canonical() for m in all_matches]
    stats = upsert_material_info(rows, data_dir=data_dir)
    trigger_set = {(m.ticker, m.fiscal_ym_guess) for m in all_matches
                   if m.fiscal_ym_guess}
    return stats, trigger_set
