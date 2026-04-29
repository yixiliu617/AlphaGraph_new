"""
Index-baseline composition fetchers.

Pulls the current member list of each tracked benchmark ETF / index and
upserts them into `universe_group_member` under the corresponding
`index_*` group_id. Companion `Listing` rows are auto-created if the
ticker isn't already known. Ticker conventions are normalized to
yfinance form (`AAPL`, `2330.TW`, `8035.T`, `005930.KS`).

Sources:
  - SMH       — iShares Semiconductor ETF holdings (BlackRock CSV)
  - SPY       — SPDR S&P 500 holdings (SSGA CSV)
  - TWSE 50   — TWSE OpenAPI 0050 ETF composition
  - Nikkei225 — Nikkei composition (HTML scrape; the official csv is
                paywalled). Falls back to a known-good static seed.
  - KOSPI200  — KRX OpenAPI

Each fetcher returns: list[tuple[ticker, weight_in_index, name]].
The seed loader doesn't run fetchers; they're invoked separately by
`backend.app.services.universe.refresh_index_baselines` which is wired
into the cron scheduler. This module is intentionally cron-runner-free
so it can also be invoked ad-hoc from a REPL or test.

NOTE: 2026-04-29 — only stub URLs / endpoints captured here; full
implementations land in week-1 day-3 of Stream 1. The seed already
contains placeholder rows for each `index_*` group so the schema and
loader work without these fetchers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Configuration — endpoint URLs (verify before live run)
# ---------------------------------------------------------------------------

# iShares (BlackRock) publishes daily holdings CSVs at predictable URLs.
# The exact filename can change; verify by visiting the ETF product page
# and copying the "Detailed Holdings and Analytics" CSV link.
SMH_HOLDINGS_CSV = (
    "https://www.ishares.com/us/products/239705/ishares-semiconductor-etf/"
    "1467271812596.ajax?fileType=csv&fileName=SOXX_holdings&dataType=fund"
)

# SSGA SPDR S&P 500 ETF holdings (XLSX; CSV available via API).
SPY_HOLDINGS_XLSX = (
    "https://www.ssga.com/us/en/individual/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
)

# TWSE OpenAPI for the 0050 ETF (Yuanta TWSE 50). Returns JSON.
# The 0050 ETF is the canonical Taiwan blue-chip index proxy.
TWSE_0050_API = "https://www.twse.com.tw/zh/page/ETF/etfDownload.html"  # ETF composition page (HTML); use TPEx OpenData where possible

# Nikkei 225 official composition page (HTML scrape; rate-limit friendly).
NIKKEI225_PAGE = "https://indexes.nikkei.co.jp/en/nkave/index/component"

# KRX (Korea Exchange) OpenAPI — KOSPI 200 components.
KRX_KOSPI200_API = "http://data.krx.co.kr/contents/MMC/SIZE/STAT/CODE.cmd"


@dataclass
class IndexMember:
    ticker: str          # yfinance form
    weight: float        # 0.0–1.0
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# SMH — iShares Semiconductor ETF
# ---------------------------------------------------------------------------

def fetch_smh_components() -> list[IndexMember]:
    """SOXX (iShares Semiconductor ETF) holdings.

    Note: ETF is SOXX, the broader name 'SMH' refers to VanEck
    Semiconductor ETF. We use SOXX here because iShares publishes a
    cleaner CSV. If pilots ask for VanEck SMH specifically, swap in
    https://www.vaneck.com/us/en/etfs/equity/smh/holdings/.
    """
    raise NotImplementedError(
        "TODO Stream 1 day 3: parse iShares SOXX CSV; ~30 names. "
        "Until then the index_smh group has only the placeholder seed row."
    )


# ---------------------------------------------------------------------------
# SPY — S&P 500 (SPDR)
# ---------------------------------------------------------------------------

def fetch_spy_components() -> list[IndexMember]:
    """SPDR S&P 500 ETF holdings. ~500 names + cash row.

    Implementation hint: SSGA ships an XLSX with a 5-row header, then
    columns [Name, Ticker, Identifier, SEDOL, Weight, Sector, Shares...].
    Drop the cash row (Identifier == 'CASH_USD'). Many tickers in the
    XLSX use SSGA convention (e.g. 'BRK.B') vs yfinance ('BRK-B'); apply
    the dot-to-dash rule for class-share tickers.
    """
    raise NotImplementedError(
        "TODO Stream 1 day 3: parse SPDR SPY XLSX; ~500 names. "
        "Until then the index_spx group has only the placeholder seed row."
    )


# ---------------------------------------------------------------------------
# TWSE 50 — Yuanta 0050 ETF
# ---------------------------------------------------------------------------

def fetch_twse_50_components() -> list[IndexMember]:
    """Top 50 TWSE blue-chips via 0050 ETF composition.

    Implementation hint: TWSE OpenAPI publishes 0050 holdings at
    https://openapi.twse.com.tw/v1/opendata/t187ap04_C — verify exact
    endpoint. Return tickers in yfinance form (e.g. '2330.TW').
    """
    raise NotImplementedError("TODO Stream 1 day 3: TWSE 0050 OpenAPI fetch.")


# ---------------------------------------------------------------------------
# Nikkei 225
# ---------------------------------------------------------------------------

def fetch_nikkei_225_components() -> list[IndexMember]:
    """Nikkei 225 composition. yfinance suffix '.T' for all 225 names.

    Implementation hint: HTML scrape of indexes.nikkei.co.jp; cache
    locally (composition changes ~3x/year on rebalance). Equal-weight
    fallback if weight not exposed (Nikkei 225 is price-weighted, not
    market-cap; weight derivation needs current prices).
    """
    raise NotImplementedError("TODO Stream 1 day 3: Nikkei 225 HTML scrape.")


# ---------------------------------------------------------------------------
# KOSPI 200
# ---------------------------------------------------------------------------

def fetch_kospi_200_components() -> list[IndexMember]:
    """KOSPI 200 composition via KRX OpenAPI. yfinance suffix '.KS'."""
    raise NotImplementedError("TODO Stream 1 day 3: KRX OpenAPI fetch.")


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

INDEX_FETCHERS = {
    "index_smh":       fetch_smh_components,
    "index_spx":       fetch_spy_components,
    "index_twse":      fetch_twse_50_components,
    "index_nikkei225": fetch_nikkei_225_components,
    "index_kospi200":  fetch_kospi_200_components,
}


def refresh_index_baselines(*, dry_run: bool = True) -> dict[str, int]:
    """Pull current composition for every tracked index and UPSERT into
    universe_group_member. Returns {group_id: count} on success.

    `dry_run=True` (default) calls the fetchers but doesn't write.
    Flip to `False` from the cron to actually persist.
    """
    out = {}
    for group_id, fn in INDEX_FETCHERS.items():
        try:
            members = fn()
            out[group_id] = len(members)
            if not dry_run:
                # TODO Stream 1 day 3: UPSERT via pg_insert(...) into
                # universe_group_member, auto-create Listings as needed.
                pass
        except NotImplementedError as e:
            out[group_id] = -1  # sentinel for "fetcher not yet implemented"
    return out
