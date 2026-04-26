"""
Platform universe registry.

Single source of truth for every company we track across markets (US / TW /
JP / KR / CN). Stored as a hand-readable CSV so it survives migrations,
diffs cleanly in git, and a human can grep / open in Excel.

File: backend/data/config/platform_universe.csv

Schema (one row per ticker):

  ticker                  e.g. "NVDA", "2330"
  name                    English name
  market                  US / TW / JP / KR / CN  (filing jurisdiction)
  exchange                NYSE / NASDAQ / TWSE / TPEx / TSE / KRX / SSE / SZSE / HKEX
  country                 ISO-3166 alpha-2 (US, TW, JP, KR, CN, ...)
  domicile                where incorporated (often != country, e.g. Cayman)
  gics_sector             GICS sector
  gics_subsector          GICS sub-sector / industry
  custom_sector           our taxonomy (Semi / Hardware / Hyperscaler /
                           Neocloud / Power / Nuclear / ...)
  custom_subsector        finer sub-grouping (Foundry / Memory / GPU rental ...)
  filing_type             10-K | 20-F | MOPS | TDnet | DART | ...
  first_imported_at       ISO date this ticker first entered the registry
  last_updated_at         ISO timestamp last time any pipeline touched this row

  -- data-coverage booleans (0/1):
  has_topline             EDGAR-derived income/CF/BS quarterly parquet exists
  has_monthly_revenue     MOPS monthly revenue parquet has rows for this ticker (TW)
  has_filings_raw         raw 10-K/10-Q parquet exists
  has_news                ticker mentioned in google_news.parquet
  has_x_posts             handle in social/x/data.parquet
  has_calendar_events     entry in earnings_calendar/events.parquet (Phase 2)
  has_earnings_releases   raw 8-K parquet under earnings_releases/

  notes                   free text

Boolean flags are stored as 0/1 for CSV-friendliness (Excel renders them
unambiguously and pandas reads them as int64 by default, which is faster
than parsing "True"/"False" strings).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Resolve relative to this file so the registry works regardless of CWD.
# parents[3] is the AlphaGraph_new project root; data lives under backend/data/.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = _PROJECT_ROOT / "backend" / "data" / "config" / "platform_universe.csv"

# All boolean coverage flags. Adding a new flag here propagates everywhere
# (read, add_ticker default, mark_data_coverage, status CLI).
_COVERAGE_FLAGS = (
    "has_topline",
    "has_monthly_revenue",
    "has_filings_raw",
    "has_news",
    "has_x_posts",
    "has_calendar_events",
    "has_earnings_releases",
)

_COLUMNS = [
    "ticker", "name", "market", "exchange", "country", "domicile",
    "gics_sector", "gics_subsector",
    "custom_sector", "custom_subsector",
    "filing_type",
    "first_imported_at", "last_updated_at",
    *_COVERAGE_FLAGS,
    "notes",
]

# Process-wide lock so concurrent calls (Taiwan scheduler thread + topline
# refresh thread + the upload-transcribe endpoint) don't race on the CSV.
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=_COLUMNS)
    for c in _COVERAGE_FLAGS:
        df[c] = df[c].astype("Int64")  # nullable int so empty frame writes clean
    return df


def read_universe(path: Path | None = None) -> pd.DataFrame:
    """Return the registry as a DataFrame (empty if the file doesn't exist).
    Boolean flags are coerced to nullable Int64 for clean filtering."""
    p = path or DEFAULT_PATH
    if not p.exists():
        return _empty_frame()
    df = pd.read_csv(p, dtype={"ticker": str})
    # Make sure every expected column exists, even on legacy CSVs.
    for col in _COLUMNS:
        if col not in df.columns:
            if col in _COVERAGE_FLAGS:
                df[col] = 0
            else:
                df[col] = ""
    for c in _COVERAGE_FLAGS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("Int64")
    return df[_COLUMNS]


def _write_universe(df: pd.DataFrame, path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    # Sort for deterministic diffs: market then ticker.
    df = df.sort_values(["market", "ticker"], kind="stable").reset_index(drop=True)
    df.to_csv(p, index=False)


def get_ticker(ticker: str, *, path: Path | None = None) -> dict[str, Any] | None:
    df = read_universe(path)
    row = df[df["ticker"] == ticker]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def add_ticker(
    ticker: str,
    *,
    name: str = "",
    market: str = "",
    exchange: str = "",
    country: str = "",
    domicile: str = "",
    gics_sector: str = "",
    gics_subsector: str = "",
    custom_sector: str = "",
    custom_subsector: str = "",
    filing_type: str = "",
    notes: str = "",
    path: Path | None = None,
    update_existing: bool = False,
) -> None:
    """Insert a new ticker row. If the ticker already exists and
    `update_existing` is False (default), the existing row is left alone
    -- callers that want to re-stamp metadata on an existing row should
    set update_existing=True. Coverage flags default to 0 on insert and
    are NEVER reset by add_ticker -- only mark_data_coverage touches them.
    """
    with _LOCK:
        df = read_universe(path)
        now = _now_iso()
        if (df["ticker"] == ticker).any():
            if not update_existing:
                return
            mask = df["ticker"] == ticker
            for col, val in (
                ("name", name), ("market", market), ("exchange", exchange),
                ("country", country), ("domicile", domicile),
                ("gics_sector", gics_sector), ("gics_subsector", gics_subsector),
                ("custom_sector", custom_sector), ("custom_subsector", custom_subsector),
                ("filing_type", filing_type), ("notes", notes),
            ):
                if val:
                    df.loc[mask, col] = val
            df.loc[mask, "last_updated_at"] = now
        else:
            new_row = {
                "ticker": ticker, "name": name, "market": market,
                "exchange": exchange, "country": country, "domicile": domicile,
                "gics_sector": gics_sector, "gics_subsector": gics_subsector,
                "custom_sector": custom_sector, "custom_subsector": custom_subsector,
                "filing_type": filing_type,
                "first_imported_at": now, "last_updated_at": now,
                "notes": notes,
            }
            for c in _COVERAGE_FLAGS:
                new_row[c] = 0
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        _write_universe(df, path)


def mark_data_coverage(
    ticker: str,
    *,
    path: Path | None = None,
    **flags: bool,
) -> None:
    """Set one or more `has_*` flags to True (or False) and bump
    `last_updated_at`. Unknown flag names raise. If the ticker isn't in
    the registry, a stub row is added so downstream pipelines can call
    this safely without pre-registering."""
    invalid = set(flags) - set(_COVERAGE_FLAGS)
    if invalid:
        raise ValueError(f"unknown coverage flags: {invalid}")

    with _LOCK:
        df = read_universe(path)
        now = _now_iso()
        if not (df["ticker"] == ticker).any():
            stub = {c: "" for c in _COLUMNS}
            stub["ticker"] = ticker
            stub["first_imported_at"] = now
            stub["last_updated_at"] = now
            for c in _COVERAGE_FLAGS:
                stub[c] = 0
            df = pd.concat([df, pd.DataFrame([stub])], ignore_index=True)
        mask = df["ticker"] == ticker
        for k, v in flags.items():
            df.loc[mask, k] = 1 if v else 0
        df.loc[mask, "last_updated_at"] = now
        _write_universe(df, path)


def list_tickers_with_flag(flag: str, *, value: bool = True, path: Path | None = None) -> list[str]:
    """Return tickers where the given coverage flag matches `value`."""
    if flag not in _COVERAGE_FLAGS:
        raise ValueError(f"unknown flag: {flag}")
    df = read_universe(path)
    target = 1 if value else 0
    return df[df[flag] == target]["ticker"].tolist()
