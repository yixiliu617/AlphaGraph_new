"""
TPEx OpenAPI current-month revenue — redundancy + cross-check path.

Endpoint: GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O
  Returns current published month's 上櫃公司每月營業收入彙總表 for ALL
  ~800 TPEx companies in one JSON call.

Role in the three-source blend (see architecture_and_design_v2.md §12):
  - PRIMARY for TPEx freshness remains MOPS t146sb05_detail (rolling 12m,
    so amendments to any of the past 12 months stay visible daily).
  - This scraper is a SECONDARY daily tick at 11:00 TPE that:
      (1) fills gaps if MOPS polling failed (redundancy)
      (2) cross-checks current-month revenue against the parquet
          and logs DIVERGENT if the two sources disagree.

Trade-offs: see `.claude/skills/taiwan-monthly-data-extraction/SKILL.md`
under "TPEx OpenAPI — secondary source".

No WAF. Plain requests. Same Python-3.13 TLS workaround as the other
TWSE/TPEx scrapers (verify=False against the government open-data host).
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import urllib3

from backend.app.services.taiwan.registry import list_watchlist_tickers
from backend.app.services.taiwan.storage import (
    DEFAULT_DATA_DIR,
    UpsertStats,
    read_monthly_revenue,
    upsert_monthly_revenue,
)

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_ENDPOINT = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Tolerate small percentage-arithmetic rounding when deciding whether
# MOPS and TPEx agree. Revenue is an integer — compare exact.
_PCT_TOLERANCE = 0.001  # 0.1 percentage point


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_current_month(*, timeout: float = 30.0) -> list[dict]:
    """Return the raw JSON rows from the TPEx OpenAPI. Empty list on error."""
    try:
        r = requests.get(
            _ENDPOINT,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            verify=False,
        )
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, list) else []
    except Exception as exc:
        logger.warning("tpex_openapi fetch failed: %s", exc)
        return []


def _roc_ym_to_ad(ym: str) -> str | None:
    """'11503' -> '2026-03'. Returns None if unparseable.

    TPEx API uses a 5-digit ROC date: 3-digit year + 2-digit month.
    """
    s = (ym or "").strip()
    if len(s) != 5 or not s.isdigit():
        return None
    roc_year = int(s[:3])
    month = int(s[3:])
    if not (1 <= month <= 12):
        return None
    return f"{roc_year + 1911:04d}-{month:02d}"


def _to_int_ktwd(val: str | int | float | None) -> int | None:
    """Parse a thousand-TWD string/number cell. Returns None on bad input."""
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_pct(val: str | None) -> float | None:
    """'45.19' / '45.19%' / '' -> 0.4519 / None."""
    if val is None:
        return None
    s = str(val).replace("%", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if abs(v) >= 999_999.99:
        return None
    return v / 100.0


def parse_to_canonical(
    rows: Iterable[dict],
    *,
    watchlist: set[str] | None = None,
) -> list[dict]:
    """Convert raw API rows into our canonical monthly_revenue schema.

    Only emits rows whose ticker is in `watchlist` (if provided). Market
    tag is always 'TPEx' for this endpoint.
    """
    out: list[dict] = []
    for r in rows:
        ticker = str(r.get("公司代號", "")).strip()
        if not ticker:
            continue
        if watchlist is not None and ticker not in watchlist:
            continue

        fiscal_ym = _roc_ym_to_ad(str(r.get("資料年月", "")))
        if not fiscal_ym:
            continue

        rev_k = _to_int_ktwd(r.get("營業收入-當月營收"))
        prior_ym_k = _to_int_ktwd(r.get("營業收入-去年當月營收"))
        ytd_k = _to_int_ktwd(r.get("累計營業收入-當月累計營收"))
        prior_ytd_k = _to_int_ktwd(r.get("累計營業收入-去年累計營收"))

        # TPEx OpenAPI values are already computed percentages (as strings).
        # We match MOPS's convention (decimal fraction, 1.0 = 100%).
        yoy_pct = _to_pct(r.get("營業收入-去年同月增減(%)"))
        mom_pct = _to_pct(r.get("營業收入-上月比較增減(%)"))
        ytd_pct = _to_pct(r.get("累計營業收入-前期比較增減(%)"))

        out.append({
            "ticker": ticker,
            "market": "TPEx",
            "fiscal_ym": fiscal_ym,
            "revenue_twd": rev_k * 1000 if rev_k is not None else None,
            "prior_year_month_twd": prior_ym_k * 1000 if prior_ym_k is not None else None,
            "cumulative_ytd_twd": ytd_k * 1000 if ytd_k is not None else None,
            "yoy_pct": yoy_pct,
            "mom_pct": mom_pct,
            "ytd_pct": ytd_pct,
        })
    return out


# ---------------------------------------------------------------------------
# Sync + cross-check
# ---------------------------------------------------------------------------

@dataclass
class Divergence:
    ticker: str
    fiscal_ym: str
    tpex_revenue: int | None
    stored_revenue: int | None


@dataclass
class SyncStats:
    fetched: int = 0
    inserted: int = 0
    matched: int = 0
    divergent: int = 0
    divergences: list[Divergence] = field(default_factory=list)


def sync_with_monthly_revenue(
    canonical_rows: list[dict],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> SyncStats:
    """Compare each canonical row vs the parquet's existing (ticker, fiscal_ym).

    Semantics:
      - No existing row → upsert (redundancy fill — MOPS path likely
        failed, or this ticker is new).
      - Existing row with identical revenue_twd → MATCHED (cross-check OK).
      - Existing row with different revenue_twd → DIVERGENT (log WARN,
        record for reporting, DO NOT overwrite — cross-source disagreement
        is a data-quality signal, not a restatement).

    Percentages are not used as the divergence trigger — they're derived
    and prone to rounding differences. Revenue is the canonical integer.
    """
    stats = SyncStats(fetched=len(canonical_rows))
    if not canonical_rows:
        return stats

    existing = read_monthly_revenue(data_dir=data_dir)
    # key the lookup by (ticker, fiscal_ym) -> revenue_twd
    existing_rev: dict[tuple[str, str], int | None] = {}
    if not existing.empty:
        for row in existing[["ticker", "fiscal_ym", "revenue_twd"]].itertuples(index=False):
            key = (str(row.ticker), str(row.fiscal_ym))
            val = row.revenue_twd
            existing_rev[key] = int(val) if pd.notna(val) else None

    to_insert: list[dict] = []
    for row in canonical_rows:
        key = (row["ticker"], row["fiscal_ym"])
        tpex_rev = row["revenue_twd"]
        if key not in existing_rev:
            to_insert.append(row)
            continue
        stored_rev = existing_rev[key]
        if stored_rev == tpex_rev:
            stats.matched += 1
        else:
            stats.divergent += 1
            stats.divergences.append(Divergence(
                ticker=row["ticker"], fiscal_ym=row["fiscal_ym"],
                tpex_revenue=tpex_rev, stored_revenue=stored_rev,
            ))
            logger.warning(
                "tpex_openapi DIVERGENT ticker=%s ym=%s  tpex_api=%s  stored=%s",
                row["ticker"], row["fiscal_ym"], tpex_rev, stored_rev,
            )

    if to_insert:
        upsert_stats = upsert_monthly_revenue(to_insert, data_dir=data_dir)
        stats.inserted = upsert_stats.inserted
        # (touched/amended from the upsert layer aren't surfaced here; the
        #  only way we'd INSERT is if no existing row was in our dict, so
        #  those counts should be 0 by construction.)

    return stats


def sync_tpex_openapi(
    *, data_dir: Path = DEFAULT_DATA_DIR,
    watchlist: set[str] | None = None,
) -> SyncStats:
    """Top-level entry point used by the scheduler.

    Fetches TPEx OpenAPI → normalises → syncs with parquet → returns
    SyncStats. Caller writes the stats to heartbeat.
    """
    if watchlist is None:
        watchlist = set(list_watchlist_tickers())
    raw = fetch_current_month()
    canonical = parse_to_canonical(raw, watchlist=watchlist)
    return sync_with_monthly_revenue(canonical, data_dir=data_dir)
