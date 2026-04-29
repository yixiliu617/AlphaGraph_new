"""
Earnings calendar API router.

Serves events from `backend/data/earnings_calendar/events.parquet`, populated
by `backend/scripts/backfill_calendar.py` (and future enrichment scripts).

Routes:
  GET /calendar/events                              all events, optional filters
  GET /calendar/events?from=&to=&market=&ticker=&status=
  GET /calendar/events/upcoming?days=30             status=upcoming|confirmed in next N days
  GET /calendar/events/recent?days=14               status=done in last N days
  GET /calendar/events/ticker/{symbol}              all events for one ticker

Schema reminder (from storage.py):
  ticker, market, fiscal_period, release_datetime_utc, release_local_tz,
  status, press_release_url, filing_url, webcast_url, transcript_url,
  dial_in_phone, dial_in_pin, source, source_id, first_seen_at, last_updated_at

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.models.api_contracts import APIResponse
from backend.app.services.calendar.storage import read_events

router = APIRouter()


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------

def _event_to_dict(row: pd.Series) -> dict:
    """Serialize a single events.parquet row to a JSON-friendly dict.
    Timestamps -> ISO strings; NaN / NaT / pd.NA -> None.

    `pd.isna()` handles every missing-value sentinel pandas supports
    (np.nan, pd.NaT, pd.NA from nullable Int / Bool dtypes, None).
    Wrap each call in try/except because pd.isna raises on some object-
    dtype values (e.g. lists). String / numeric / Timestamp dispatched
    to JSON-friendly forms; everything else passes through.
    """
    out: dict = {}
    for col in row.index:
        v = row[col]
        # Cheap check first, then pd.isna() for the array-aware sentinels.
        if v is None:
            out[col] = None
            continue
        try:
            if pd.isna(v):
                out[col] = None
                continue
        except (TypeError, ValueError):
            pass  # v is something pd.isna doesn't accept — keep as-is

        if isinstance(v, pd.Timestamp):
            out[col] = v.isoformat()
            continue
        out[col] = v
    return out


def _events_to_payload(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    df = df.sort_values(
        ["release_datetime_utc", "ticker"],
        ascending=[False, True],
        na_position="last",
    )
    return [_event_to_dict(r) for _, r in df.iterrows()]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/events", response_model=APIResponse)
def list_events(
    from_date: str | None = Query(None, alias="from",
                                  description="ISO date YYYY-MM-DD; release_datetime_utc >= this"),
    to_date:   str | None = Query(None, alias="to",
                                  description="ISO date YYYY-MM-DD; release_datetime_utc <= this"),
    market:    str | None = Query(None, description="US | TW | JP | KR"),
    ticker:    str | None = Query(None, description="Single ticker filter"),
    status:    str | None = Query(None, description="upcoming | confirmed | done"),
    limit:     int  = Query(2000, ge=1, le=5000),
) -> APIResponse:
    """Query events with optional filters. All filters AND-combined.
    Sorted by release_datetime_utc DESC (newest first)."""
    try:
        from_dt = (
            datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
            if from_date else None
        )
        to_dt = (
            datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
            if to_date else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    df = read_events(
        market=market.upper() if market else None,
        status=status.lower() if status else None,
        ticker=ticker.upper() if ticker else None,
        from_date=from_dt,
        to_date=to_dt,
    )
    payload = _events_to_payload(df)[:limit]
    return APIResponse(success=True, data=payload, meta={"count": len(payload)})


@router.get("/events/upcoming", response_model=APIResponse)
def upcoming_events(
    days: int = Query(30, ge=1, le=365),
    market: str | None = None,
) -> APIResponse:
    """Events with status in {upcoming, confirmed} occurring in the next N days."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    df = read_events(
        market=market.upper() if market else None,
        from_date=now,
        to_date=end,
    )
    df = df[df["status"].isin(["upcoming", "confirmed"])]
    payload = _events_to_payload(df)
    return APIResponse(success=True, data=payload, meta={"count": len(payload)})


@router.get("/events/recent", response_model=APIResponse)
def recent_events(
    days: int = Query(14, ge=1, le=365),
    market: str | None = None,
) -> APIResponse:
    """Events with status=done that happened in the last N days."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    df = read_events(
        market=market.upper() if market else None,
        from_date=start,
        to_date=now,
        status="done",
    )
    payload = _events_to_payload(df)
    return APIResponse(success=True, data=payload, meta={"count": len(payload)})


@router.get("/events/ticker/{symbol}", response_model=APIResponse)
def ticker_events(
    symbol: str,
    limit: int = Query(50, ge=1, le=500),
) -> APIResponse:
    """All known calendar events for `symbol`, newest first."""
    df = read_events(ticker=symbol.upper())
    payload = _events_to_payload(df)[:limit]
    return APIResponse(success=True, data=payload, meta={
        "ticker": symbol.upper(),
        "count": len(payload),
    })
