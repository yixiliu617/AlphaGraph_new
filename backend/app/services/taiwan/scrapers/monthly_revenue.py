"""
MOPS monthly-revenue scraper (Path A — JSON API).

Endpoint:
  POST https://mops.twse.com.tw/mops/api/t146sb05_detail
  body: {"company_id": "2330"}

Response shape: `result.data` is a list of 12 positional rows, most
recent first, in the form:
  [roc_year, month, revenue_ktwd, prior_yr_month_ktwd, yoy_pct_str,
   ytd_ktwd, prior_yr_ytd_ktwd, ytd_yoy_pct_str]

All monetary values are **thousand TWD** — we multiply by 1000 when
storing so downstream math is in full TWD.

See .claude/skills/taiwan-monthly-data-extraction/SKILL.md for endpoint
catalogue, ROC calendar handling, and WAF workarounds.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import (
    list_watchlist_tickers,
    load_mops_master,
)
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    upsert_monthly_revenue,
    write_raw_capture,
    UpsertStats,
)
from backend.app.services.taiwan.validation import validate_monthly_revenue_row

logger = logging.getLogger(__name__)

_DETAIL_PATH = "/mops/api/t146sb05_detail"

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _parse_int(text: str) -> int | None:
    text = (text or "").replace(",", "").strip()
    if not text or text in ("-", "—"):
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return int(float(m.group()))
    except ValueError:
        return None


def _parse_pct(text: str) -> float | None:
    """Accept '45.19%' or '45.19' or '-12.5' or '−12.5'. Return decimal (0.4519)."""
    text = (text or "").replace("−", "-").replace("%", "").strip()
    if not text or text in ("-", "—"):
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    # MOPS sentinel: 999999.99 means "cannot compute" (divide-by-zero / overflow)
    if abs(v) >= 999_999.99:
        return None
    return v / 100.0


def _roc_ym_to_ad(roc_year: str, month: str) -> str | None:
    """'115', '3' -> '2026-03'. Returns None if inputs are malformed."""
    try:
        ad_year = int(roc_year.strip()) + 1911
        m = int(month.strip())
        if not (1 <= m <= 12):
            return None
        return f"{ad_year:04d}-{m:02d}"
    except (ValueError, AttributeError):
        return None


def parse_detail_rows(
    data_rows: list[list[str]],
    *,
    ticker: str,
    market: str,
) -> list[dict]:
    """Normalise raw `result.data` rows into our canonical schema.

    Rows are returned oldest-first so MoM computation (which needs the
    immediately preceding month) can walk the list linearly.
    """
    out: list[dict] = []
    for row in data_rows:
        if len(row) < 8:
            continue
        ym = _roc_ym_to_ad(row[0], row[1])
        if not ym:
            continue
        rev_k = _parse_int(row[2])
        prior_k = _parse_int(row[3])
        ytd_k = _parse_int(row[5])
        out.append({
            "ticker": ticker,
            "market": market,
            "fiscal_ym": ym,
            "revenue_twd": rev_k * 1000 if rev_k is not None else None,
            "prior_year_month_twd": prior_k * 1000 if prior_k is not None else None,
            "yoy_pct": _parse_pct(row[4]),
            "cumulative_ytd_twd": ytd_k * 1000 if ytd_k is not None else None,
            "ytd_pct": _parse_pct(row[7]),
            "mom_pct": None,  # filled below
        })
    # MOPS returns newest-first; we store oldest-first for easier MoM.
    out.sort(key=lambda r: r["fiscal_ym"])
    # MoM computed locally — the API does not expose it.
    for i in range(1, len(out)):
        prev = out[i - 1]["revenue_twd"]
        cur = out[i]["revenue_twd"]
        if prev and cur:
            out[i]["mom_pct"] = cur / prev - 1
    return out


def scrape_monthly_revenue_ticker(
    client: MopsClient,
    ticker: str,
    *,
    market: str,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> UpsertStats:
    """Scrape one ticker's 12-month monthly-revenue history and upsert."""
    res = client.post_json(_DETAIL_PATH, {"company_id": ticker})
    if res.status_code != 200:
        logger.warning("monthly_revenue fetch failed ticker=%s status=%d",
                       ticker, res.status_code)
        return UpsertStats()

    try:
        body = res.json()
    except ValueError:
        logger.warning("monthly_revenue non-JSON body ticker=%s first200=%r",
                       ticker, res.text[:200])
        return UpsertStats()

    if body.get("code") != 200:
        logger.warning("monthly_revenue api error ticker=%s code=%s msg=%s",
                       ticker, body.get("code"), body.get("message"))
        return UpsertStats()

    # Raw capture for audit. Key by (ticker, latest_ym) so re-fetches of
    # the same 12-month window overwrite in place; history parquet
    # captures the earlier versions if content changes.
    data_rows = body.get("result", {}).get("data", []) or []
    if not data_rows:
        return UpsertStats()

    latest = data_rows[0]
    latest_ym = _roc_ym_to_ad(latest[0], latest[1]) or "unknown"
    write_raw_capture(
        source="monthly_revenue",
        ticker=ticker,
        key=f"{latest_ym}_detail",
        content=res.raw_bytes or res.text.encode("utf-8"),
        data_dir=data_dir,
    )

    parsed = parse_detail_rows(data_rows, ticker=ticker, market=market)
    for r in parsed:
        r["parse_flags"] = [f.value for f in validate_monthly_revenue_row(r)]
    stats = upsert_monthly_revenue(parsed, data_dir=data_dir)
    logger.info("monthly_revenue ticker=%s market=%s rows=%d stats=%s",
                ticker, market, len(parsed), stats)
    return stats


def scrape_monthly_revenue_watchlist(
    client: MopsClient,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> UpsertStats:
    """Scrape every watchlist ticker. Requires company_master populated
    (for market resolution). Rows missing market default to 'Unknown'.
    """
    total = UpsertStats()
    watchlist = list_watchlist_tickers()
    master = load_mops_master()
    market_by_ticker: dict[str, str] = {}
    if not master.empty and "co_id" in master.columns and "market" in master.columns:
        market_by_ticker = dict(zip(master["co_id"].astype(str),
                                    master["market"].astype(str)))

    for t in watchlist:
        market = market_by_ticker.get(t, "Unknown")
        stats = scrape_monthly_revenue_ticker(client, t, market=market,
                                              data_dir=data_dir)
        total.inserted += stats.inserted
        total.touched += stats.touched
        total.amended += stats.amended

    logger.info("monthly_revenue watchlist done tickers=%d stats=%s",
                len(watchlist), total)
    return total
