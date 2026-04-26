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

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

router = APIRouter()

DATA_ROOT = Path("backend/data/financials")
FACTS_PARQUET = DATA_ROOT / "quarterly_facts" / "2454.TW.parquet"


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
