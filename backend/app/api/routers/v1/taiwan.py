"""
Read-only Taiwan disclosure endpoints.

This router does NOT scrape. It reads parquet / SQLite written by the
taiwan_scheduler process. Humans hit these through the Next.js dashboard;
external agents call them via API.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.core.config import settings
from backend.app.services.data_cache import read_parquet_cached
from backend.app.models.api_contracts import APIResponse
from backend.app.services.taiwan import registry, storage
from backend.app.services.taiwan.health import read_all_heartbeats

_DAY_TRADING_DIR  = Path("backend/data/taiwan/day_trading")
_FOREIGN_FLOW_DIR = Path("backend/data/taiwan/foreign_flow")

router = APIRouter()


def _sqlite_conn() -> sqlite3.Connection:
    uri = settings.POSTGRES_URI
    if not uri.startswith("sqlite:///"):
        raise RuntimeError("Taiwan heartbeat currently SQLite-only; migrate to Alembic for Postgres.")
    return sqlite3.connect(uri.replace("sqlite:///", ""))


@router.get("/watchlist", response_model=APIResponse)
def list_watchlist():
    df = registry.load_watchlist()
    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/monthly-revenue", response_model=APIResponse)
def list_monthly_revenue(
    tickers: str = Query(..., description="Comma-separated tickers"),
    months: int = Query(12, ge=1, le=120, description="Trailing months"),
):
    want = {t.strip() for t in tickers.split(",") if t.strip()}
    df = storage.read_monthly_revenue()
    if df.empty:
        return APIResponse(success=True, data=[])
    df = df[df["ticker"].isin(want)].copy()
    # Take the latest `months` periods per ticker.
    df = df.sort_values(["ticker", "fiscal_ym"], ascending=[True, False])
    df = df.groupby("ticker", group_keys=False).head(months)
    df = df.sort_values(["ticker", "fiscal_ym"])  # final chronological order per ticker

    # Convert timestamps to iso strings for JSON
    for col in ("first_seen_at", "last_seen_at"):
        if col in df.columns:
            df[col] = df[col].astype(str)

    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/ticker/{ticker}", response_model=APIResponse)
def get_ticker(ticker: str):
    wl = registry.load_watchlist()
    match = wl[wl["ticker"] == ticker]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not in watchlist.")
    meta = match.iloc[0].to_dict()
    df = storage.read_monthly_revenue()
    latest = None
    if not df.empty:
        mine = df[df["ticker"] == ticker].sort_values("fiscal_ym", ascending=False)
        if not mine.empty:
            latest_row = mine.iloc[0].to_dict()
            # stringify timestamps
            for col in ("first_seen_at", "last_seen_at"):
                if col in latest_row:
                    latest_row[col] = str(latest_row[col])
            latest = latest_row

    data = {**meta, "latest_revenue": latest}
    return APIResponse(success=True, data=data)


# ---------------------------------------------------------------------------
# TWSE day-trading statistics (TWTB4U) — summary time-series + per-day detail
# ---------------------------------------------------------------------------

@router.get("/day-trading/summary", response_model=APIResponse)
def list_day_trading_summary(
    start: Optional[str] = Query(None, description="YYYY-MM-DD inclusive (default: 1 year ago)"),
    end:   Optional[str] = Query(None, description="YYYY-MM-DD inclusive (default: today)"),
):
    """Market-wide day-trading % of volume, one row per trading day."""
    path = _DAY_TRADING_DIR / "summary.parquet"
    if not path.exists():
        return APIResponse(success=True, data=[])
    df = read_parquet_cached(path)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    df = df.sort_values("date")
    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/day-trading/detail", response_model=APIResponse)
def list_day_trading_detail(
    date: str = Query(..., description="YYYY-MM-DD"),
    limit: int = Query(200, ge=1, le=2000),
    sort: str = Query("buy_value_twd", description="buy_value_twd | sell_value_twd | shares"),
):
    """Per-ticker day-trading stats for one trading date, top-N by selected metric."""
    path = _DAY_TRADING_DIR / "detail.parquet"
    if not path.exists():
        return APIResponse(success=True, data=[])
    df = read_parquet_cached(path)
    df = df[df["date"] == date]
    if df.empty:
        return APIResponse(success=True, data=[])
    if sort not in {"buy_value_twd", "sell_value_twd", "shares"}:
        raise HTTPException(status_code=400, detail=f"invalid sort: {sort}")
    df = df.sort_values(sort, ascending=False).head(limit)
    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/day-trading/dates", response_model=APIResponse)
def list_day_trading_dates():
    """Distinct trading dates available in the detail parquet (descending)."""
    path = _DAY_TRADING_DIR / "detail.parquet"
    if not path.exists():
        return APIResponse(success=True, data=[])
    dates = sorted(read_parquet_cached(path, columns=["date"])["date"].astype(str).unique(),
                   reverse=True)
    return APIResponse(success=True, data=dates)


# ---------------------------------------------------------------------------
# TWSE 三大法人 buy/sell flow (BFI82U) — long-format daily series.
# ---------------------------------------------------------------------------

@router.get("/foreign-flow", response_model=APIResponse)
def list_foreign_flow(
    start: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    end:   Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    investor_types: Optional[str] = Query(
        None,
        description="Comma list to filter, e.g. 'foreign,trust'. Default: all types.",
    ),
):
    """Daily 三大法人 buy/sell/net by investor_type. Long format."""
    path = _FOREIGN_FLOW_DIR / "data.parquet"
    if not path.exists():
        return APIResponse(success=True, data=[])
    df = read_parquet_cached(path)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    if investor_types:
        wanted = {t.strip() for t in investor_types.split(",") if t.strip()}
        df = df[df["investor_type"].isin(wanted)]
    df = df.sort_values(["date", "investor_type"])
    return APIResponse(success=True, data=df.to_dict(orient="records"))


@router.get("/foreign-flow/dates", response_model=APIResponse)
def list_foreign_flow_dates():
    """Distinct trading dates available in the foreign-flow parquet (descending)."""
    path = _FOREIGN_FLOW_DIR / "data.parquet"
    if not path.exists():
        return APIResponse(success=True, data=[])
    dates = sorted(read_parquet_cached(path, columns=["date"])["date"].astype(str).unique(),
                   reverse=True)
    return APIResponse(success=True, data=dates)


@router.get("/health", response_model=APIResponse)
def scraper_health():
    try:
        conn = _sqlite_conn()
    except Exception as exc:
        return APIResponse(success=True, data={"scrapers": [], "error": str(exc)})
    try:
        rows = read_all_heartbeats(conn)
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    # Annotate each with a lag_seconds since last success.
    annotated = []
    for r in rows:
        lag = None
        if r.get("last_success_at"):
            try:
                ts = datetime.fromisoformat(r["last_success_at"])
                lag = int((now - ts).total_seconds())
            except ValueError:
                lag = None
        r["lag_seconds"] = lag
        annotated.append(r)

    return APIResponse(success=True, data={"scrapers": annotated})
