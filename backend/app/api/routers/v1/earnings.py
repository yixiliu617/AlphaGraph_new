"""
Earnings releases API router.

Serves the 8-K Item 2.02 exhibits stored by
`backend/scripts/ingest_earnings_releases.py` as browsable documents.

Routes:
  GET /earnings/releases                         list all (lightweight, no text_raw)
  GET /earnings/releases?ticker=NVDA             filter by ticker
  GET /earnings/releases/{release_id}            full text for one release

Data source:
  backend/data/earnings_releases/ticker=*.parquet

Release id format: "{TICKER}:{accession_no}:{exhibit}"
  — composite primary key across all stored exhibit rows.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

from backend.app.models.api_contracts import APIResponse

router = APIRouter()

# parents: [0]=v1 [1]=routers [2]=api [3]=app [4]=backend [5]=repo root
_REPO_ROOT    = Path(__file__).resolve().parents[5]
_RELEASES_DIR = _REPO_ROOT / "backend" / "data" / "earnings_releases"
_CALC_DIR     = _REPO_ROOT / "backend" / "data" / "filing_data" / "calculated"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_exhibit(exhibit: str) -> str:
    """EX-99.01 and EX-99..1 -> EX-99.1 (strip zero padding and dot typos)."""
    m = re.match(r"EX-99\.+0*(\d+)", exhibit or "")
    return f"EX-99.{m.group(1)}" if m else (exhibit or "")


def _doc_type_label(exhibit: str) -> str:
    """Human label for an exhibit number. EX-99.1 -> 'Press Release' etc."""
    norm = _normalize_exhibit(exhibit)
    return {
        "EX-99.1": "Press Release",
        "EX-99.2": "CFO Commentary",
    }.get(norm, f"Exhibit {norm}" if norm else "Exhibit")


def _build_fiscal_map(ticker: str) -> list[tuple[pd.Timestamp, str]]:
    """
    Return a list of (end_date, fiscal_label) for a ticker, sorted ascending.
    Quarterly standalone rows only (skip Annual and is_ytd rows).
    fiscal_label = "FY{fiscal_year}-Q{n}".
    Used to map an 8-K filing_date to the fiscal quarter it's reporting on.
    """
    path = _CALC_DIR / f"ticker={ticker}.parquet"
    if not path.exists():
        return []
    try:
        df = read_parquet_cached(path, columns=["end_date", "fiscal_year", "fiscal_quarter", "is_ytd"])
    except Exception:
        return []
    df = df[
        df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
        & (~df["is_ytd"].astype(bool))
        & df["fiscal_year"].notna()
        & df["end_date"].notna()
    ].copy()
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date")
    out: list[tuple[pd.Timestamp, str]] = []
    for _, r in df.iterrows():
        try:
            fy = int(r["fiscal_year"])
        except (TypeError, ValueError):
            continue
        out.append((r["end_date"], f"FY{fy}-{r['fiscal_quarter']}"))
    return out


def _fiscal_period_for(filing_date: pd.Timestamp, fmap: list[tuple[pd.Timestamp, str]]) -> str | None:
    """
    Map a release's filing_date to the most recent fiscal quarter that
    closed strictly BEFORE that date. Earnings 8-Ks are always filed a few
    days to a few weeks after the quarter ends.
    """
    if not fmap:
        return None
    candidate: str | None = None
    for end_date, label in fmap:
        if end_date <= filing_date:
            candidate = label
        else:
            break
    return candidate


def _make_id(ticker: str, accession_no: str, exhibit: str) -> str:
    return f"{ticker}:{accession_no}:{exhibit}"


def _parse_id(release_id: str) -> tuple[str, str, str]:
    parts = release_id.split(":", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail=f"Invalid release id: {release_id}")
    return parts[0].upper(), parts[1], parts[2]


def _ymd(val) -> str:
    try:
        return pd.Timestamp(val).strftime("%Y-%m-%d")
    except Exception:
        return str(val)[:10]


def _build_title(ticker: str, exhibit: str, fiscal_period: str | None, filing_date_str: str) -> str:
    label = _doc_type_label(exhibit)
    period = fiscal_period or filing_date_str
    return f"[{ticker}] {label} · {period} ({filing_date_str})"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/releases", response_model=APIResponse)
def list_releases(
    ticker: str | None = Query(None, description="Filter to a single ticker"),
    limit:  int  = Query(2000, ge=1, le=5000),
) -> APIResponse:
    if not _RELEASES_DIR.exists():
        return APIResponse(success=True, data=[])

    files = sorted(_RELEASES_DIR.glob("ticker=*.parquet"))
    if ticker:
        want = ticker.upper().strip()
        files = [p for p in files if p.stem == f"ticker={want}"]

    stubs: list[dict] = []
    for p in files:
        t = p.stem.replace("ticker=", "")
        try:
            df = read_parquet_cached(
                p,
                columns=[
                    "accession_no", "exhibit", "filing_date",
                    "period_of_report", "text_chars", "url",
                ],
            )
            fmap = _build_fiscal_map(t)
            for _, r in df.iterrows():
                exhibit      = str(r["exhibit"])
                filing_date  = pd.Timestamp(r["filing_date"])
                fiscal_label = _fiscal_period_for(filing_date, fmap)
                filing_ymd   = _ymd(filing_date)
                stubs.append({
                    "id":               _make_id(t, str(r["accession_no"]), exhibit),
                    "ticker":           t,
                    "exhibit":          exhibit,
                    "exhibit_norm":     _normalize_exhibit(exhibit),
                    "doc_type_label":   _doc_type_label(exhibit),
                    "title":            _build_title(t, exhibit, fiscal_label, filing_ymd),
                    "filing_date":      filing_ymd,
                    "period_of_report": _ymd(r["period_of_report"]),
                    "fiscal_period":    fiscal_label,
                    "text_chars":       int(r.get("text_chars", 0) or 0),
                    "url":              (str(r["url"]) if pd.notna(r.get("url")) else None),
                })
        except Exception:
            # Don't let one bad ticker take down the whole endpoint.
            # TODO: surface via logger once we wire one in here.
            continue

    stubs.sort(key=lambda s: s["filing_date"], reverse=True)
    return APIResponse(success=True, data=stubs[:limit])


@router.get("/releases/{release_id}", response_model=APIResponse)
def get_release(release_id: str) -> APIResponse:
    ticker, accession, exhibit = _parse_id(release_id)
    path = _RELEASES_DIR / f"ticker={ticker}.parquet"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No earnings releases for ticker {ticker}")

    df = read_parquet_cached(path)
    match = df[
        (df["accession_no"].astype(str) == accession)
        & (df["exhibit"].astype(str) == exhibit)
    ]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"Release not found: {release_id}")

    r = match.iloc[0]
    filing_date = pd.Timestamp(r["filing_date"])
    fiscal_label = _fiscal_period_for(filing_date, _build_fiscal_map(ticker))
    filing_ymd   = _ymd(filing_date)

    return APIResponse(success=True, data={
        "id":               release_id,
        "ticker":           ticker,
        "exhibit":          exhibit,
        "exhibit_norm":     _normalize_exhibit(exhibit),
        "doc_type_label":   _doc_type_label(exhibit),
        "title":            _build_title(ticker, exhibit, fiscal_label, filing_ymd),
        "filing_date":      filing_ymd,
        "period_of_report": _ymd(r["period_of_report"]),
        "fiscal_period":    fiscal_label,
        "items":            str(r.get("items", "")),
        "description":      str(r.get("description", "")),
        "document":         str(r.get("document", "")),
        "text_chars":       int(r.get("text_chars", 0) or 0),
        "text_raw":         str(r.get("text_raw", "")),
        "url":              (str(r["url"]) if pd.notna(r.get("url")) else None),
    })
