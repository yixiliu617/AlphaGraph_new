"""
Equity prices API router.

Two endpoints, ticker-parameterised so they scale to the full universe
without per-ticker boilerplate:

  GET /api/v1/prices/{ticker}/daily?days=N
      Daily OHLCV. Default `days=365`. Returns oldest-first (chart-friendly,
      per the project's time-axis-sort-convention rule).

  GET /api/v1/prices/{ticker}/intraday?bars=N&interval=15m
      Intraday bars. Default `bars=200`, `interval=15m`. Returns
      oldest-first.

  GET /api/v1/prices/{ticker}/stats
      Key stats card payload: latest close, %change vs prior session,
      52-week range, total return 1Y, ADV (20-day avg dollar volume).

Response shape (daily / intraday):
    {
      "ticker": "NVDA",
      "interval": "1d",
      "rows": [{"t": "2026-04-24T00:00:00Z", "o": 105.1, "h": 106.4,
                "l": 104.2, "c": 106.0, "v": 145000000, "ac": 106.0}, ...]
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Path as PathParam, Query

from backend.app.services.data_cache import read_parquet_cached


router = APIRouter()


_PRICES_DIR = Path("backend/data/financials/prices")
_INTRADAY_DIR = _PRICES_DIR / "intraday"


def _daily_path(ticker: str) -> Path:
    return _PRICES_DIR / f"{ticker}.parquet"


def _intraday_path(ticker: str, interval: str = "15m") -> Path:
    return _INTRADAY_DIR / f"{ticker}_{interval}.parquet"


@router.get("/{ticker}/daily")
def get_daily_prices(
    ticker: str = PathParam(..., description="Yahoo-format ticker, e.g. NVDA or 2330.TW"),
    days: int = Query(365, ge=1, le=10000),
):
    p = _daily_path(ticker)
    if not p.exists():
        raise HTTPException(404, f"No daily prices for {ticker}")

    df = read_parquet_cached(p, columns=["date", "open", "high", "low", "close",
                                         "adj_close", "volume"])
    if df.empty:
        return {"ticker": ticker, "interval": "1d", "rows": []}

    cutoff = df["date"].max() - pd.Timedelta(days=days)
    df = df[df["date"] >= cutoff].sort_values("date")

    rows = [
        {
            "t": pd.Timestamp(r["date"]).isoformat(),
            "o": float(r["open"]) if pd.notna(r["open"]) else None,
            "h": float(r["high"]) if pd.notna(r["high"]) else None,
            "l": float(r["low"]) if pd.notna(r["low"]) else None,
            "c": float(r["close"]) if pd.notna(r["close"]) else None,
            "ac": float(r["adj_close"]) if pd.notna(r["adj_close"]) else None,
            "v": int(r["volume"]) if pd.notna(r["volume"]) else 0,
        }
        for r in df.to_dict(orient="records")
    ]
    return {"ticker": ticker, "interval": "1d", "rows": rows}


@router.get("/{ticker}/intraday")
def get_intraday_prices(
    ticker: str = PathParam(..., description="Yahoo-format ticker"),
    bars: int = Query(200, ge=1, le=10000),
    interval: str = Query("15m", pattern="^(15m|30m|60m|1h)$"),
):
    p = _intraday_path(ticker, interval)
    if not p.exists():
        raise HTTPException(404, f"No intraday prices for {ticker}@{interval}")

    df = read_parquet_cached(p, columns=["ts_utc", "open", "high", "low", "close", "volume"])
    if df.empty:
        return {"ticker": ticker, "interval": interval, "rows": []}

    df = df.sort_values("ts_utc").tail(bars)
    rows = [
        {
            "t": pd.Timestamp(r["ts_utc"]).isoformat(),
            "o": float(r["open"]) if pd.notna(r["open"]) else None,
            "h": float(r["high"]) if pd.notna(r["high"]) else None,
            "l": float(r["low"]) if pd.notna(r["low"]) else None,
            "c": float(r["close"]) if pd.notna(r["close"]) else None,
            "v": int(r["volume"]) if pd.notna(r["volume"]) else 0,
        }
        for r in df.to_dict(orient="records")
    ]
    return {"ticker": ticker, "interval": interval, "rows": rows}


@router.get("/{ticker}/stats")
def get_price_stats(
    ticker: str = PathParam(...),
):
    """Key-stats card payload: latest close, prior-session close, 52w range,
    1Y return, ADV (20-day avg dollar volume)."""
    p = _daily_path(ticker)
    if not p.exists():
        raise HTTPException(404, f"No daily prices for {ticker}")

    df = read_parquet_cached(p, columns=["date", "close", "adj_close", "volume"])
    if df.empty or len(df) < 2:
        raise HTTPException(404, f"Insufficient daily prices for {ticker}")

    df = df.sort_values("date")
    last = df.iloc[-1]
    prev = df.iloc[-2]
    last_date = pd.Timestamp(last["date"])

    one_year_ago = last_date - pd.Timedelta(days=365)
    last_year_window = df[df["date"] >= one_year_ago]
    if last_year_window.empty:
        last_year_window = df
    high_52w = float(last_year_window["close"].max())
    low_52w = float(last_year_window["close"].min())
    first_in_window = last_year_window.iloc[0]

    # Adjusted-close return for total-return calc.
    adj_now = float(last["adj_close"]) if pd.notna(last["adj_close"]) else float(last["close"])
    adj_then = float(first_in_window["adj_close"]) if pd.notna(first_in_window["adj_close"]) else float(first_in_window["close"])
    one_year_return_pct = ((adj_now / adj_then) - 1.0) * 100 if adj_then else None

    # 20-day average dollar volume
    last_20 = df.tail(20)
    adv = float((last_20["close"] * last_20["volume"]).mean()) if not last_20.empty else None

    last_close = float(last["close"]) if pd.notna(last["close"]) else None
    prev_close = float(prev["close"]) if pd.notna(prev["close"]) else None
    change_pct = (
        ((last_close / prev_close) - 1.0) * 100
        if last_close is not None and prev_close
        else None
    )

    return {
        "ticker": ticker,
        "as_of": last_date.isoformat(),
        "last_close": last_close,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "one_year_return_pct": one_year_return_pct,
        "avg_dollar_volume_20d": adv,
        "history_days": int((last_date - pd.Timestamp(df["date"].min())).days),
    }
