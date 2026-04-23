"""
Registry: reads the curated watchlist CSV + MOPS company-master parquet.

Provides lookups the scrapers need:
  - list_watchlist_tickers()          -> list[str]
  - watchlist_to_mops_ids(watchlist)  -> dict[ticker, mops_row]
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

WATCHLIST_CSV = Path(__file__).resolve().parents[3] / "data" / "taiwan" / "watchlist_semi.csv"
REGISTRY_PARQUET = Path(__file__).resolve().parents[3] / "data" / "taiwan" / "_registry" / "mops_company_master.parquet"


def load_watchlist() -> pd.DataFrame:
    return pd.read_csv(WATCHLIST_CSV, dtype=str).fillna("")


def list_watchlist_tickers() -> list[str]:
    return load_watchlist()["ticker"].tolist()


def load_mops_master() -> pd.DataFrame:
    if not REGISTRY_PARQUET.exists():
        return pd.DataFrame(columns=["co_id", "name_zh", "industry_zh", "market", "last_seen_at"])
    return pd.read_parquet(REGISTRY_PARQUET)


def save_mops_master(df: pd.DataFrame) -> None:
    REGISTRY_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(REGISTRY_PARQUET, index=False)
