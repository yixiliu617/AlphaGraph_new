"""
Read-only TSMC data endpoints — serves what's already scraped + extracted
into backend/data/financials/{quarterly_facts,transcripts,guidance,raw}/.

Mirrors the Taiwan disclosure router pattern: read parquet, return JSON.
No scraping happens here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

router = APIRouter()

DATA_ROOT = Path("backend/data/financials")
FACTS_PARQUET = DATA_ROOT / "quarterly_facts" / "2330.TW.parquet"
TRANSCRIPTS_PARQUET = DATA_ROOT / "transcripts" / "2330.TW.parquet"
GUIDANCE_PARQUET = DATA_ROOT / "guidance" / "2330.TW.parquet"
RAW_INDEX = DATA_ROOT / "raw" / "2330.TW" / "_index.json"


def _safe_records(df: pd.DataFrame) -> list[dict]:
    """to_dict('records') with NaN -> None and date -> ISO string."""
    if df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif out[col].dtype == "object":
            # date / np.datetime / Timestamp inside object columns
            out[col] = out[col].apply(
                lambda v: v.isoformat() if hasattr(v, "isoformat") else v
            )
    out = out.replace({np.nan: None})
    return out.to_dict(orient="records")


@router.get("/summary")
def summary():
    """High-level stats so the UI can show 'we have X reports, Y facts' etc."""
    out: dict = {"ticker": "2330.TW", "layers": {}}

    if FACTS_PARQUET.exists():
        df = read_parquet_cached(FACTS_PARQUET)
        out["layers"]["quarterly_facts"] = {
            "rows": len(df),
            "metrics": df["metric"].nunique(),
            "periods": df["period_label"].nunique(),
            "earliest_period_end": str(df["period_end"].min()),
            "latest_period_end": str(df["period_end"].max()),
            "source_reports": df["source"].nunique(),
        }
    if TRANSCRIPTS_PARQUET.exists():
        tdf = read_parquet_cached(TRANSCRIPTS_PARQUET)
        out["layers"]["transcripts"] = {
            "rows": len(tdf),
            "quarters": tdf["period_label"].nunique(),
            "earliest_call": str(tdf["event_date"].min()) if "event_date" in tdf.columns else None,
            "latest_call": str(tdf["event_date"].max()) if "event_date" in tdf.columns else None,
            "speakers": tdf["speaker_name"].nunique(),
            "total_chars": int(tdf["char_count"].sum()) if "char_count" in tdf.columns else None,
        }
    if GUIDANCE_PARQUET.exists():
        gdf = read_parquet_cached(GUIDANCE_PARQUET)
        out["layers"]["guidance"] = {
            "rows": len(gdf),
            "periods_covered": gdf["period_label"].nunique(),
            "pages": gdf["guidance_issued_at"].nunique() if "guidance_issued_at" in gdf.columns else None,
            "earliest_page": str(gdf["guidance_issued_at"].min()) if "guidance_issued_at" in gdf.columns else None,
            "latest_page": str(gdf["guidance_issued_at"].max()) if "guidance_issued_at" in gdf.columns else None,
        }
    if RAW_INDEX.exists():
        idx = json.loads(RAW_INDEX.read_text(encoding="utf-8"))
        quarters = idx.get("quarters", {})
        n_pdfs = sum(len(q.get("pdfs", [])) for q in quarters.values())
        out["layers"]["pdf_catalog"] = {
            "quarters": len(quarters),
            "pdfs": n_pdfs,
        }
    return out


@router.get("/facts")
def facts(
    metric: Optional[str] = Query(None, description="Filter by metric name (substring)"),
    dimension: Optional[str] = Query(None, description="Filter by dimension"),
    headline_only: bool = Query(False, description="Only headline metrics (dimension='')"),
    limit: int = Query(2000, le=20000),
):
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    if metric:
        df = df[df["metric"].str.contains(metric, case=False, na=False)]
    if dimension is not None:
        df = df[df["dimension"] == dimension]
    if headline_only:
        df = df[df["dimension"] == ""]
    df = df.sort_values(["period_end", "metric", "dimension"], ascending=[False, True, True])
    df = df.head(limit)
    return {
        "ticker": "2330.TW",
        "total": len(df),
        "facts": _safe_records(df),
    }


@router.get("/financials/wide")
def financials_wide(
    quarters: int = Query(20, ge=1, le=200, description="Number of most-recent quarters"),
):
    """Pivot the headline P&L metrics into a wide table for easy display.
    One row per metric, one column per period_label."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)

    HEADLINE = [
        "net_revenue", "net_revenue_usd", "cost_of_revenue", "gross_profit", "gross_margin",
        "r_and_d", "sga", "operating_expenses", "operating_income", "operating_margin",
        "net_income", "net_profit_margin", "eps", "wafer_shipment",
        "capex", "capex_usd", "free_cash_flow", "ending_cash_balance",
    ]
    # Pre-aggregate (mean per period across overlapping sources)
    sub = df[(df["dimension"] == "") & (df["metric"].isin(HEADLINE))].copy()
    if sub.empty:
        return {"ticker": "2330.TW", "metrics": [], "periods": []}
    agg = sub.groupby(["metric", "period_label", "period_end"], as_index=False)["value"].mean()
    # Pick latest N periods
    period_order = (
        agg[["period_label", "period_end"]]
        .drop_duplicates()
        .sort_values("period_end", ascending=False)
        .head(quarters)
    )
    chosen_periods = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen_periods)]
    # Pivot
    piv = agg.pivot(index="metric", columns="period_label", values="value")
    # Reorder rows to match HEADLINE order; cols chronological
    piv = piv.reindex(HEADLINE).reindex(columns=chosen_periods)
    # Attach unit per metric (from first row found)
    units = sub.groupby("metric")["unit"].first().to_dict()

    out_rows = []
    for m in HEADLINE:
        if m not in piv.index:
            continue
        row = {"metric": m, "unit": units.get(m, "")}
        for q in chosen_periods:
            v = piv.loc[m, q] if q in piv.columns else np.nan
            row[q] = None if pd.isna(v) else float(v)
        out_rows.append(row)

    return {
        "ticker": "2330.TW",
        "periods": chosen_periods,
        "metrics": out_rows,
    }


@router.get("/segments")
def segments(
    metric: str = Query(
        "revenue_share_by_technology",
        description="Which segment metric to pivot",
    ),
    quarters: int = Query(20, ge=1, le=200),
):
    """Pivot a segment metric (revenue_share_by_*) into a wide table.
    Columns = chronological period_labels, rows = dimensions."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    sub = df[df["metric"] == metric].copy()
    if sub.empty:
        return {"metric": metric, "dimensions": [], "periods": [], "values": []}
    # Defensive: parser drift on a couple of older reports has tagged section
    # headers (e.g. "Wafer Revenue by Technology") as dimensions with garbage
    # values like 2024.0. Real revenue-share rows are always in [0, 100]; drop
    # anything outside that bound. Also drop dimensions that are clearly the
    # wrong metric's labels (the canonical lists per metric below).
    CANONICAL: dict[str, set[str]] = {
        "revenue_share_by_geography": {
            "North America", "Asia Pacific", "China", "Japan", "EMEA",
        },
        "revenue_share_by_platform": {
            "High Performance Computing", "Smartphone", "Internet of Things",
            "Automotive", "Digital Consumer Electronics", "Others",
            # Older reports used short labels:
            "HPC", "IoT", "DCE",
        },
    }
    # Tech mix doesn't have a fixed dimension list (TSMC adds new nodes
    # like 3nm, 2nm, 1.6nm over time). Filter by shape: dimension ends in
    # 'nm' or 'um' (after stripping ' and above'). Catches every node
    # naming convention TSMC has used while still rejecting platform labels
    # like 'Communication' / 'HPC' that the parser sometimes drifts into.
    if metric == "revenue_share_by_technology":
        d = sub["dimension"].astype(str).str.lower().str.replace(" and above", "", regex=False).str.strip()
        sub = sub[d.str.endswith(("nm", "um"))]
    # Tech-mix legacy collapses: pre-1Q25 reports listed "16nm" + "20nm" as
    # separate rows; from 1Q25 onwards TSMC merged them into "16/20nm". Drop
    # the legacy individuals when "16/20nm" exists in the dataset, otherwise
    # the output sums to ~109% for affected quarters.
    sub = sub[(sub["value"] >= 0) & (sub["value"] <= 100)]
    if metric == "revenue_share_by_technology":
        if "16/20nm" in set(sub["dimension"].unique()):
            sub = sub[~sub["dimension"].isin({"16nm", "20nm"})]
    if metric in CANONICAL:
        sub = sub[sub["dimension"].isin(CANONICAL[metric])]
    if sub.empty:
        return {"metric": metric, "dimensions": [], "periods": [], "values": []}
    agg = sub.groupby(["dimension", "period_label", "period_end"], as_index=False)["value"].mean()
    period_order = (
        agg[["period_label", "period_end"]].drop_duplicates()
        .sort_values("period_end", ascending=False).head(quarters)
    )
    chosen = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen)]
    piv = agg.pivot(index="dimension", columns="period_label", values="value")
    piv = piv.reindex(columns=chosen)
    # Order dimensions by latest-period value desc
    # chosen[0] is the most recent period — order dimensions by latest-quarter value desc.
    piv = piv.sort_values(by=chosen[0], ascending=False)
    rows = [
        {"dimension": str(idx), **{q: (None if pd.isna(piv.loc[idx, q]) else float(piv.loc[idx, q])) for q in chosen}}
        for idx in piv.index
    ]
    return {"metric": metric, "periods": chosen, "rows": rows}


@router.get("/guidance")
def guidance(
    quarters: int = Query(20, ge=1, le=200),
):
    """Historical guidance vs actual for every metric TSMC publishes. One row
    per (period_label, metric) with actual + low/high/midpoint + outcome
    label + percent deviations. The same-page constraint
    (`issued_at == period_end`) ensures we're comparing each quarter's
    actual against the original guidance set 3 months earlier (which the
    page restates alongside the actual)."""
    if not GUIDANCE_PARQUET.exists():
        raise HTTPException(404, detail="guidance parquet not found")
    df = read_parquet_cached(GUIDANCE_PARQUET)
    df = df.sort_values("period_end", ascending=False)
    df_same = df[df["guidance_issued_at"].astype(str) == df["period_end"].astype(str)].copy()
    metrics = ["revenue", "gross_margin", "operating_margin", "usd_ntd_avg_rate"]
    out_rows = []
    for plabel, pe in (
        df_same[["period_label", "period_end"]].drop_duplicates()
        .sort_values("period_end", ascending=False).head(quarters).itertuples(index=False)
    ):
        slc = df_same[(df_same["period_label"] == plabel)]
        for m in metrics:
            ms = slc[slc["metric"] == m]
            if ms.empty:
                continue
            actual = ms[ms["bound"] == "actual"]["value"]
            low = ms[ms["bound"] == "low"]["value"]
            high = ms[ms["bound"] == "high"]["value"]
            point = ms[ms["bound"] == "point"]["value"]    # FX is a single point, not range
            row: dict = {
                "period_label": plabel,
                "period_end": str(pe),
                "metric": m,
                "actual":      float(actual.iloc[0]) if len(actual) else None,
                "guide_low":   float(low.iloc[0])    if len(low)    else None,
                "guide_high":  float(high.iloc[0])   if len(high)   else None,
                "guide_point": float(point.iloc[0])  if len(point)  else None,
                "unit": ms["unit"].iloc[0] if len(ms) else None,
            }
            row["guide_mid"] = (
                (row["guide_low"] + row["guide_high"]) / 2.0
                if row["guide_low"] is not None and row["guide_high"] is not None
                else row["guide_point"]
            )
            a, lo, hi, mid = row["actual"], row["guide_low"], row["guide_high"], row["guide_mid"]
            if a is None or lo is None or hi is None:
                row["outcome"] = None
            elif a > hi:
                row["outcome"] = "BEAT high"
            elif a < lo:
                row["outcome"] = "MISS low"
            else:
                row["outcome"] = "in range"
            # Percent deviations. Units differ (revenue=usd_b, margins=pct,
            # fx=ntd_per_usd) but a relative % deviation is meaningful for all
            # — for a margin actual of 66.2 vs mid 64.0, vs_mid_pct = +3.4%
            # (i.e. actual was 3.4% higher than the midpoint).
            row["vs_mid_pct"]  = ((a - mid) / mid * 100) if a is not None and mid not in (None, 0) else None
            row["vs_high_pct"] = ((a - hi)  / hi  * 100) if a is not None and hi  not in (None, 0) else None
            # For margins, also surface the absolute pp delta which analysts
            # find easier to read than relative %.
            row["vs_mid_pp"]   = (a - mid) if a is not None and mid is not None else None
            row["vs_high_pp"]  = (a - hi)  if a is not None and hi  is not None else None
            out_rows.append(row)
    return {"rows": out_rows}


@router.get("/guidance/forward")
def guidance_forward():
    """Most recent forward guidance (next quarter)."""
    if not GUIDANCE_PARQUET.exists():
        raise HTTPException(404, detail="guidance parquet not found")
    df = read_parquet_cached(GUIDANCE_PARQUET)
    df = df.sort_values("guidance_issued_at", ascending=False)
    if df.empty:
        return {"period_label": None, "rows": []}
    latest_page = df["guidance_issued_at"].max()
    latest = df[df["guidance_issued_at"] == latest_page]
    forward = latest[latest["period_end"].astype(str) > str(latest_page)]
    rows = [
        {
            "period_label": r["period_label"],
            "metric": r["metric"],
            "bound": r["bound"],
            "value": float(r["value"]),
            "unit": r["unit"],
        }
        for _, r in forward.iterrows()
    ]
    return {
        "issued_at": str(latest_page),
        "for_period": forward["period_label"].iloc[0] if len(forward) else None,
        "rows": rows,
    }


@router.get("/transcripts/quarters")
def transcript_quarters():
    """List quarters with transcripts."""
    if not TRANSCRIPTS_PARQUET.exists():
        raise HTTPException(404, detail="transcripts parquet not found")
    df = read_parquet_cached(TRANSCRIPTS_PARQUET)
    out = (
        df.groupby(["period_label", "period_end"], as_index=False)
          .agg(turns=("turn_index", "count"), chars=("char_count", "sum"),
               event_date=("event_date", "max"))
    )
    out = out.sort_values("period_end", ascending=False)
    out["period_end"] = out["period_end"].astype(str)
    out["event_date"] = out["event_date"].astype(str)
    return {"quarters": _safe_records(out)}


@router.get("/transcripts/turns")
def transcript_turns(
    period_label: str = Query(..., description="e.g. 1Q26"),
):
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
    mask = df["text"].str.contains(q, case=False, na=False)
    hits = df[mask].sort_values("period_end", ascending=False).head(limit).copy()
    # Trim text to a snippet around the match
    def snippet(t):
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


@router.get("/pdfs")
def pdf_catalog():
    """The full PDF catalog (year/quarter -> 5 PDFs each)."""
    if not RAW_INDEX.exists():
        raise HTTPException(404, detail="_index.json not found")
    return json.loads(RAW_INDEX.read_text(encoding="utf-8"))
