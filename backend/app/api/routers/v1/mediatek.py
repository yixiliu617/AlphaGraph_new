"""
Read-only MediaTek (2454.TW) data endpoints — serves what's already
extracted into backend/data/financials/quarterly_facts/2454.TW.parquet.

Scope vs TSMC + UMC routers:
- /summary, /financials/wide, /quarters  -> implemented
- /segments/*  -> NOT implemented; MediaTek doesn't publish segment-share
  breakdowns in the press release. The Presentation slide deck has
  smartphone / computing-connectivity / power-IC charts but those are
  visual and out of scope for v1.
- /transcripts/*  -> NOT implemented; MediaTek's transcript PDF format
  diverges from TSMC's LSEG layout (different speaker-turn syntax).
  Defer to a transcript-extractor task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import json
import re

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

router = APIRouter()

DATA_ROOT = Path("backend/data/financials")
FACTS_PARQUET = DATA_ROOT / "quarterly_facts" / "2454.TW.parquet"
TRANSCRIPTS_PARQUET = DATA_ROOT / "transcripts" / "2454.TW.parquet"
GUIDANCE_PARQUET = DATA_ROOT / "guidance" / "2454.TW.parquet"
PDF_INDEX = DATA_ROOT / "raw" / "2454.TW" / "_index.json"
SOURCE_ISSUES = DATA_ROOT / "raw" / "2454.TW" / "_source_issues.json"


def _safe_records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif out[col].dtype == "object":
            out[col] = out[col].apply(
                lambda v: v.isoformat() if hasattr(v, "isoformat") else v
            )
    out = out.replace({np.nan: None})
    return out.to_dict(orient="records")


@router.get("/summary")
def summary():
    out: dict = {
        "ticker": "2454.TW",
        "layers": {},
        "notes": {
            "segments": "MediaTek's press release doesn't include segment-"
                        "share tables. Application mix lives on the "
                        "Presentation slides as charts.",
            "transcripts": "MediaTek publishes its own English Transcript "
                           "PDF starting 2021Q2; format diverges from TSMC's "
                           "LSEG transcripts and isn't yet ingested.",
        },
    }

    if FACTS_PARQUET.exists():
        df = read_parquet_cached(FACTS_PARQUET)
        out["layers"]["quarterly_facts"] = {
            "rows": len(df),
            "metrics": int(df["metric"].nunique()),
            "periods": int(df["period_label"].nunique()),
            "earliest_period_end": str(df["period_end"].min()),
            "latest_period_end": str(df["period_end"].max()),
            "source_reports": int(df["source"].nunique()),
        }
    return out


@router.get("/facts")
def facts(
    metric: Optional[str] = Query(None),
    headline_only: bool = Query(False),
    limit: int = Query(2000, le=20000),
):
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    if metric:
        df = df[df["metric"].str.contains(metric, case=False, na=False)]
    if headline_only:
        df = df[df["dimension"] == ""]
    df = df.sort_values(["period_end", "metric"], ascending=[False, True])
    df = df.head(limit)
    return {"ticker": "2454.TW", "total": len(df), "facts": _safe_records(df)}


@router.get("/financials/wide")
def financials_wide(
    quarters: int = Query(20, ge=1, le=200),
):
    """Pivot MediaTek's headline P&L metrics into a wide table.
    Currency unit is NT$ million (`ntd_m`).

    Like TSMC and UMC, the same period appears across multiple sources
    (curQ in its own report, prevQ in the next, YoY a year later).
    Aggregation is `mean` so overlapping values (which should agree
    exactly modulo restatement) collapse to one column."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)

    HEADLINE = [
        "net_revenue",
        "cost_of_revenue",
        "gross_profit",
        "selling_expenses",
        "g_and_a",
        "r_and_d",
        "operating_expenses",
        "operating_income",
        "non_operating_items",
        "net_income_before_tax",
        "income_tax_expense",
        "net_income",
        "net_income_attributable",
        "eps",
        "gross_margin",
        "operating_margin",
        "net_profit_margin",
        "operating_cash_flow",
    ]
    sub = df[(df["dimension"] == "") & (df["metric"].isin(HEADLINE))].copy()
    if sub.empty:
        return {"ticker": "2454.TW", "metrics": [], "periods": []}
    agg = sub.groupby(["metric", "period_label", "period_end"], as_index=False)["value"].mean()
    period_order = (
        agg[["period_label", "period_end"]]
        .drop_duplicates()
        .sort_values("period_end", ascending=False)
        .head(quarters)
    )
    chosen = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen)]
    piv = agg.pivot(index="metric", columns="period_label", values="value")
    piv = piv.reindex(HEADLINE).reindex(columns=chosen)
    units = sub.groupby("metric")["unit"].first().to_dict()

    rows = []
    for m in HEADLINE:
        if m not in piv.index:
            continue
        r = {"metric": m, "unit": units.get(m, "")}
        for q in chosen:
            v = piv.loc[m, q] if q in piv.columns else np.nan
            r[q] = None if pd.isna(v) else float(v)
        rows.append(r)
    return {"ticker": "2454.TW", "periods": chosen, "metrics": rows}


@router.get("/quarters")
def quarters_index():
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    g = (
        df.groupby(["period_label", "period_end"], as_index=False)
          .agg(
              fact_count=("value", "size"),
              metrics=("metric", "nunique"),
              sources=("source", lambda s: sorted(s.unique().tolist())),
          )
    )
    g = g.sort_values("period_end", ascending=False)
    g["period_end"] = g["period_end"].astype(str)
    return {"ticker": "2454.TW", "quarters": _safe_records(g)}


# ---------------------------------------------------------------------------
# Source-issue ledger — surfaces known per-quarter problems with the IR
# site (e.g. wrong-file-uploaded-at-source) so the frontend can render an
# explanatory banner. The source ledger is read every request (small JSON).
# ---------------------------------------------------------------------------

def _load_source_issues() -> list[dict]:
    if not SOURCE_ISSUES.exists():
        return []
    return json.loads(SOURCE_ISSUES.read_text(encoding="utf-8")).get("issues", [])


def _issues_for(period_label: str | None = None, file_type: str | None = None) -> list[dict]:
    issues = _load_source_issues()
    out = []
    for it in issues:
        if period_label and it.get("period_label") != period_label:
            continue
        if file_type and it.get("file_type") != file_type:
            continue
        out.append(it)
    return out


@router.get("/source-issues")
def source_issues_all():
    """All known source-side data quality issues for MediaTek's IR site,
    keyed by (period_label, file_type). Used by the frontend to render
    per-quarter banners explaining gaps."""
    return {"ticker": "2454.TW", "issues": _load_source_issues()}


# ---------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------

@router.get("/transcripts/quarters")
def transcript_quarters():
    """List quarters that have transcripts ingested. Newest-first.

    Includes a `source_issues` array surfacing any quarters where the IR
    site has known problems (e.g. wrong-file-uploaded-at-source). The
    frontend renders a banner per affected quarter.
    """
    if not TRANSCRIPTS_PARQUET.exists():
        raise HTTPException(404, detail="transcripts parquet not found")
    df = read_parquet_cached(TRANSCRIPTS_PARQUET)
    out = (
        df.groupby(["period_label", "period_end"], as_index=False)
          .agg(turns=("turn_index", "count"),
               chars=("char_count", "sum"),
               event_date=("event_date", "max"),
               speakers=("speaker_name", "nunique"))
    )
    out = out.sort_values("period_end", ascending=False)
    out["period_end"] = out["period_end"].astype(str)
    out["event_date"] = out["event_date"].astype(str)
    return {
        "quarters": _safe_records(out),
        "source_issues": _issues_for(file_type="transcript"),
    }


@router.get("/transcripts/turns")
def transcript_turns(period_label: str = Query(..., description="e.g. 4Q25")):
    if not TRANSCRIPTS_PARQUET.exists():
        raise HTTPException(404, detail="transcripts parquet not found")
    df = read_parquet_cached(TRANSCRIPTS_PARQUET)
    sub = df[df["period_label"] == period_label].sort_values("turn_index")
    return {"period_label": period_label, "turns": _safe_records(sub)}


@router.get("/transcripts/search")
def transcript_search(
    q: str = Query(..., min_length=2, description="case-insensitive substring"),
    limit: int = Query(50, ge=1, le=500),
):
    if not TRANSCRIPTS_PARQUET.exists():
        raise HTTPException(404, detail="transcripts parquet not found")
    df = read_parquet_cached(TRANSCRIPTS_PARQUET)
    mask = df["text"].str.contains(q, case=False, na=False, regex=False)
    hits = df[mask].sort_values("period_end", ascending=False).head(limit).copy()

    def snippet(t: str) -> str:
        i = t.lower().find(q.lower())
        if i < 0:
            return t[:200]
        s = max(0, i - 80)
        e = min(len(t), i + len(q) + 200)
        return ("…" if s > 0 else "") + t[s:e] + ("…" if e < len(t) else "")

    hits["snippet"] = hits["text"].apply(snippet)
    cols = ["period_label", "period_end", "speaker_name", "speaker_role", "section", "snippet"]
    hits = hits[cols]
    return {"query": q, "matches": _safe_records(hits)}


# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------

def _actual_for(df: pd.DataFrame, period_label: str, metric: str) -> float | None:
    sub = df[(df["period_label"] == period_label) & (df["metric"] == metric)
             & (df["dimension"] == "")]
    if sub.empty:
        return None
    return float(sub["value"].mean())


def _gross_margin_actual(df: pd.DataFrame, period_label: str) -> float | None:
    gp = _actual_for(df, period_label, "gross_profit")
    nr = _actual_for(df, period_label, "net_revenue")
    if gp is None or nr is None or nr == 0:
        return None
    return gp / nr * 100.0


def _revenue_actual_ntd_b(df: pd.DataFrame, period_label: str) -> float | None:
    """Revenue actual converted from silver units (NT$ million) to NT$ billion
    to match MediaTek's guidance unit."""
    nr = _actual_for(df, period_label, "net_revenue")
    return None if nr is None else nr / 1000.0


def _period_sort_key(label: str) -> tuple[int, int]:
    qm = re.match(r"(\d)Q(\d{2})", label)
    if qm:
        q = int(qm.group(1))
        yy = int(qm.group(2))
        year = 2000 + yy if yy < 50 else 1900 + yy
        return (year, q)
    return (0, 0)


@router.get("/guidance")
def guidance(quarters: int = Query(20, ge=1, le=200)):
    """Forward guidance issued in each transcript vs the realized actual.
    Sorted newest-first by for_period.

    Comparable metrics:
      - guidance_revenue (NT$ B): compared against net_revenue / 1000
      - guidance_gross_margin (%): compared against gross_profit / net_revenue
      - guidance_usd_ntd_avg_rate: verbal-only (MediaTek doesn't disclose
        the realized FX rate in the press release — it's mentioned in the
        following quarter's transcript prose, deferred for v1)
    """
    if not GUIDANCE_PARQUET.exists():
        raise HTTPException(404, detail="guidance parquet not found")
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="facts parquet not found")
    g_df = read_parquet_cached(GUIDANCE_PARQUET)
    f_df = read_parquet_cached(FACTS_PARQUET)

    keys = ["issued_in_period_label", "for_period_label", "metric"]
    g_df = g_df.sort_values("for_period_label", ascending=False)

    GUIDANCE_TO_ACTUAL = {
        "guidance_revenue":          ("revenue_b", None),
        "guidance_gross_margin":     ("derived_gross_margin", None),
        "guidance_usd_ntd_avg_rate": ("n/a", None),
    }

    rows: list[dict] = []
    for (issued, for_p, metric), grp in g_df.groupby(keys):
        bounds = {r["bound"]: r for _, r in grp.iterrows()}
        verbal = bounds.get("verbal", {}).get("text") if "verbal" in bounds else None
        lo  = bounds.get("low",  {}).get("value") if "low"  in bounds else None
        hi  = bounds.get("high", {}).get("value") if "high" in bounds else None
        mid = bounds.get("midpoint", {}).get("value") if "midpoint" in bounds else None
        pt  = bounds.get("point", {}).get("value") if "point" in bounds else None

        strategy, _ = GUIDANCE_TO_ACTUAL.get(metric, ("n/a", None))
        actual = None
        if strategy == "revenue_b":
            actual = _revenue_actual_ntd_b(f_df, for_p)
        elif strategy == "derived_gross_margin":
            actual = _gross_margin_actual(f_df, for_p)

        outcome = None
        vs_mid_pct = None
        vs_mid_pp = None
        if actual is not None and lo is not None and hi is not None:
            if actual > hi:
                outcome = "BEAT high"
            elif actual < lo:
                outcome = "MISS low"
            else:
                outcome = "in range"
            if mid is not None:
                vs_mid_pp = actual - mid
                if mid != 0:
                    vs_mid_pct = (actual - mid) / mid * 100.0

        unit = grp["unit"].iloc[0]
        rows.append({
            "issued_in_period": issued,
            "for_period": for_p,
            "metric": metric,
            "verbal": verbal,
            "guide_low": lo,
            "guide_mid": mid,
            "guide_high": hi,
            "guide_point": pt,
            "actual": actual,
            "outcome": outcome,
            "vs_mid_pct": vs_mid_pct,
            "vs_mid_pp": vs_mid_pp,
            "unit": unit,
        })

    rows.sort(key=lambda r: (
        _period_sort_key(r["for_period"]),
        _period_sort_key(r["issued_in_period"]),
        r["metric"],
    ), reverse=True)
    rows = rows[:quarters * 4]
    return {"ticker": "2454.TW", "rows": rows}


@router.get("/pdfs")
def pdf_catalog():
    """Full catalog of every IR PDF MediaTek has published — Press Release,
    Presentation, Transcript, Financial Statements, Earnings Call Invitation
    (for upcoming quarters), and the TWSE-mandated Consolidated /
    Unconsolidated Financial Reports.

    Source: scrape of https://www.mediatek.com/investor-relations/financial-information
    cached at backend/data/financials/raw/2454.TW/_index.json. Refresh via
    `tools/mediatek_refresh_pdf_index.py` (or rerun the inline script).

    Per-quarter PDFs vary by era:
      2022+ : 5 quarterly types + 1-2 financial reports per quarter
      2021Q1: prepared_remarks instead of transcript
      pre-2021Q2: no transcript published
      pre-2017: 'Investor Conference Report' / 'Material' folder shape
                (not yet classified into the typed schema)

    The 'upcoming quarter' pattern: the earnings_call_invitation lands ~3
    weeks before the call, BEFORE the rest of the quarter's PDFs. So the
    most-recent quarter often shows only `earnings_call_invitation` until
    the call happens, then the other 4-5 PDFs land within hours.
    """
    if not PDF_INDEX.exists():
        raise HTTPException(404, detail="MediaTek PDF index not found; run the index-refresh script.")
    return json.loads(PDF_INDEX.read_text(encoding="utf-8"))
