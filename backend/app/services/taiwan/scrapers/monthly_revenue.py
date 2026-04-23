"""
MOPS monthly-revenue scraper.

Endpoint (summary query, one call per market-month returns all companies):
  POST https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs
  form: step=1&functionName=t05st10_ifrs&TYPEK=sii&year=YYYY&month=MM&co_id=

Post-processing:
  - Parse HTML table; strip thousands separators ("," in Western digits; 千 in Chinese digits).
  - Percentages in MOPS are strings like "33.33" meaning 33.33 %; we store as floats
    where 1.0 = 100 %.
  - Filter to watchlist tickers only — the raw response includes all listed
    companies (~1,000 for TWSE, ~800 for TPEx).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import list_watchlist_tickers
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    upsert_monthly_revenue,
    write_raw_capture,
    UpsertStats,
)
from backend.app.services.taiwan.validation import validate_monthly_revenue_row

logger = logging.getLogger(__name__)

_URL = "https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs"

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _parse_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    m = _NUM_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return int(float(m.group()))
    except ValueError:
        return None


def _parse_pct(text: str) -> float | None:
    """Accept '33.33' / '33.33%' / '−12.5'. Return decimal (0.3333) or None."""
    text = (text or "").replace("−", "-").replace("%", "").strip()
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def parse_monthly_revenue_html(html: str, *, market: str, year: int, month: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    fiscal_ym = f"{year:04d}-{month:02d}"
    rows: list[dict] = []

    for table in soup.select("table"):
        first_row = table.find("tr")
        if not first_row:
            continue
        header_cells = first_row.find_all(["th", "td"])
        headers = [th.get_text(strip=True) for th in header_cells]
        if "公司代號" not in headers or "當月營收" not in headers:
            continue

        idx = {h: i for i, h in enumerate(headers)}

        for tr in table.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= idx["當月營收"]:
                continue
            ticker = cells[idx["公司代號"]].strip()
            if not ticker:
                continue
            rows.append({
                "ticker": ticker,
                "market": market,
                "fiscal_ym": fiscal_ym,
                "revenue_twd": _parse_int(cells[idx["當月營收"]]),
                "prior_year_month_twd": _parse_int(cells[idx.get("去年當月營收", -1)]) if "去年當月營收" in idx else None,
                "cumulative_ytd_twd": _parse_int(cells[idx.get("當月累計營收", -1)]) if "當月累計營收" in idx else None,
                "mom_pct": _parse_pct(cells[idx.get("上月比較增減(%)", -1)]) if "上月比較增減(%)" in idx else None,
                "yoy_pct": _parse_pct(cells[idx.get("去年同月增減(%)", -1)]) if "去年同月增減(%)" in idx else None,
                "ytd_pct": _parse_pct(cells[idx.get("前期比較增減(%)", -1)]) if "前期比較增減(%)" in idx else None,
            })
    return rows


def scrape_monthly_revenue_market_month(
    client: MopsClient,
    *,
    year: int,
    month: int,
    market: str,
    data_dir: Path = DEFAULT_DATA_DIR,
    watchlist: list[str] | None = None,
) -> UpsertStats:
    """Scrape one (market, year, month) MOPS query, filter to watchlist, upsert."""
    watchlist = watchlist or list_watchlist_tickers()
    market_code = "sii" if market == "TWSE" else "otc" if market == "TPEx" else market
    form = {"step": "1", "functionName": "t05st10_ifrs",
            "TYPEK": market_code, "year": str(year), "month": f"{month:02d}", "co_id": ""}

    result = client.post(_URL, data=form, allow_browser_fallback=True)
    if result.status_code != 200 or not result.text:
        logger.warning("monthly_revenue fetch failed market=%s ym=%04d-%02d status=%d",
                       market, year, month, result.status_code)
        return UpsertStats()

    # Raw capture (per market-month).
    write_raw_capture(
        source="monthly_revenue",
        ticker=f"_all_{market}",
        key=f"{year:04d}-{month:02d}",
        content=(result.raw_bytes or result.text.encode("utf-8")),
        data_dir=data_dir,
    )

    parsed = parse_monthly_revenue_html(result.text, market=market, year=year, month=month)
    filtered = [r for r in parsed if r["ticker"] in set(watchlist)]
    # Validation flags are informational — we still store flagged rows.
    for r in filtered:
        r["parse_flags"] = [f.value for f in validate_monthly_revenue_row(r)]
    stats = upsert_monthly_revenue(filtered, data_dir=data_dir)
    logger.info("monthly_revenue market=%s ym=%04d-%02d stats=%s raw_all_rows=%d matched_watchlist=%d",
                market, year, month, stats, len(parsed), len(filtered))
    return stats
