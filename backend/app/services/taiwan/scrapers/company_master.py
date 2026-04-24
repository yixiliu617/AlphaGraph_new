"""
MOPS company-master resolver.

The 2024 MOPS redesign removed the bulk `ajax_t51sb01` endpoint that
returned the full listing roster as HTML. The new API is per-ticker:
we resolve each watchlist ticker via `/mops/api/KeywordsQuery` and take
the market + sector tag off the response's `companyList[].title`.

Write target: `backend/data/taiwan/_registry/mops_company_master.parquet`
Schema: co_id, name_zh, industry_zh, market, last_seen_at

Market mapping (from the title prefix that MOPS returns):
    上市   -> TWSE      (sii)
    上櫃   -> TPEx      (otc)
    興櫃   -> Emerging
    公開發行 -> Public   (non-listed public companies)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import pandas as pd

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import (
    list_watchlist_tickers,
    save_mops_master,
)

logger = logging.getLogger(__name__)

_KEYWORDS_URL = "/mops/api/KeywordsQuery"

# Market code is the leading prefix of the `title` field.
_MARKET_PREFIXES = (
    ("上市", "TWSE"),
    ("上櫃", "TPEx"),
    ("興櫃", "Emerging"),
    ("公開發行", "Public"),
)


def _split_market_sector(title: str) -> tuple[str, str]:
    """Return (market, sector_zh). Unknown prefixes -> ('Unknown', <full title>)."""
    title = (title or "").strip()
    for prefix, market in _MARKET_PREFIXES:
        if title.startswith(prefix):
            return market, title[len(prefix):].strip()
    return "Unknown", title


_TICKER_NAME_RE = re.compile(r"^\s*(\d{4,6})\s+(.+?)\s*$")


def _parse_ticker_name(result_str: str) -> tuple[str, str]:
    """Parse 'ticker name_zh' strings. E.g. '2330 台灣積體電路製造股份有限公司'."""
    m = _TICKER_NAME_RE.match(result_str or "")
    if not m:
        return "", (result_str or "").strip()
    return m.group(1), m.group(2)


def resolve_ticker(client: MopsClient, ticker: str) -> dict | None:
    """Hit KeywordsQuery for one ticker. Returns a master row or None on miss."""
    res = client.post_json(_KEYWORDS_URL, {"queryFunction": True, "keyword": ticker})
    if res.status_code != 200:
        logger.warning("KeywordsQuery failed ticker=%s status=%d", ticker, res.status_code)
        return None
    try:
        body = res.json()
    except ValueError:
        logger.warning("KeywordsQuery non-JSON body ticker=%s first120=%r", ticker, res.text[:120])
        return None
    if body.get("code") != 200:
        logger.warning("KeywordsQuery api error ticker=%s code=%s msg=%s",
                       ticker, body.get("code"), body.get("message"))
        return None

    company_list = body.get("result", {}).get("companyList", []) or []
    for group in company_list:
        title = group.get("title", "")
        market, sector = _split_market_sector(title)
        for entry in group.get("data", []) or []:
            found_ticker, name_zh = _parse_ticker_name(entry.get("result", ""))
            if found_ticker == ticker:
                return {
                    "co_id": ticker,
                    "name_zh": name_zh,
                    "industry_zh": sector,
                    "market": market,
                }
    logger.warning("KeywordsQuery returned no match for ticker=%s", ticker)
    return None


def scrape_company_master(client: MopsClient, tickers: list[str] | None = None) -> int:
    """Resolve every watchlist ticker via KeywordsQuery and persist.

    Returns the number of rows written.
    """
    tickers = tickers or list_watchlist_tickers()
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for t in tickers:
        row = resolve_ticker(client, t)
        if row is None:
            continue
        row["last_seen_at"] = now
        rows.append(row)

    df = pd.DataFrame(rows, columns=["co_id", "name_zh", "industry_zh", "market", "last_seen_at"])
    save_mops_master(df)
    logger.info("company_master resolved=%d/%d", len(rows), len(tickers))
    return len(df)
