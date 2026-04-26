"""
Read-only UMC (2303.TW) data endpoints — serves what's already extracted into
backend/data/financials/quarterly_facts/2303.TW.parquet.

Scope vs TSMC router:
- /summary, /financials/wide, /segments  -> implemented (mirrors TSMC pattern,
  adapted to UMC's currency unit `ntd_m` and 4-dimension segment breakdown).
- /transcripts/*                          -> NOT implemented; UMC's
  `conference_call.pdf` is a 1-page calendar invitation, not a transcript.
  Surface this explicitly via /summary so the UI can render an "unavailable"
  message rather than a broken tab.
- /guidance/*                             -> NOT implemented; UMC publishes
  guidance verbally on the call (Word slides) without a structured table.
  Defer until we have a guidance-extraction strategy specific to UMC.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.app.services.data_cache import read_parquet_cached

router = APIRouter()

DATA_ROOT = Path("backend/data/financials")
FACTS_PARQUET = DATA_ROOT / "quarterly_facts" / "2303.TW.parquet"
GUIDANCE_PARQUET = DATA_ROOT / "guidance" / "2303.TW.parquet"


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
    """High-level stats so the UI can render the header card."""
    out: dict = {
        "ticker": "2303.TW",
        "layers": {},
        "notes": {
            "transcripts": "UMC does not publish earnings call transcripts. "
                           "The 'conference_call.pdf' on UMC's IR site is a "
                           "calendar invitation only.",
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
    dimension: Optional[str] = Query(None),
    headline_only: bool = Query(False),
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
    return {"ticker": "2303.TW", "total": len(df), "facts": _safe_records(df)}


@router.get("/financials/wide")
def financials_wide(
    quarters: int = Query(20, ge=1, le=200),
):
    """Pivot UMC's headline P&L metrics into a wide table for display.
    Currency unit is NT$ million (`ntd_m`) — frontend should label as such.

    UMC's reports are 3-period (curQ, prevQ, YoY) so the same period appears
    across multiple sources (3Q25 in the 3Q25 report's curQ slot AND in the
    4Q25 report's prevQ slot AND the 3Q26 report's YoY slot once published).
    Aggregating with `mean` gives identical values where they overlap and
    fills any single-source gaps.
    """
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)

    HEADLINE = [
        "net_revenue",
        "gross_profit",
        "operating_expenses",
        "other_operating_income",
        "operating_income",
        "non_operating_items",
        "net_income",
        "eps",
        "eps_adr",
        "usd_ntd_avg_rate",
    ]
    sub = df[(df["dimension"] == "") & (df["metric"].isin(HEADLINE))].copy()
    if sub.empty:
        return {"ticker": "2303.TW", "metrics": [], "periods": []}
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
    return {"ticker": "2303.TW", "periods": chosen, "metrics": rows}


@router.get("/segments")
def segments(
    metric: str = Query(
        "revenue_share_by_technology",
        description="One of: revenue_share_by_technology, _by_geography, "
                    "_by_customer_type, _by_application",
    ),
    quarters: int = Query(20, ge=1, le=200),
):
    """Pivot a UMC segment-share metric into a wide table.
    Same shape as TSMC /segments — dimensions ordered by latest-period
    desc, period columns chronological.

    Defensive cleaning:
    - Drop rows outside [0, 100] (parser drift safety net).
    - For technology (Geometry) shares, restrict to dimensions that look
      like node specs (end with 'nm', 'um', or contain '<x<=').
    """
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    sub = df[df["metric"] == metric].copy()
    if sub.empty:
        return {"metric": metric, "dimensions": [], "periods": [], "rows": []}

    sub = sub[(sub["value"] >= 0) & (sub["value"] <= 100)]

    if metric == "revenue_share_by_technology":
        d = sub["dimension"].astype(str).str.lower()
        node_like = (
            d.str.endswith("nm")
            | d.str.endswith("um")
            | d.str.contains("<x<=", regex=False)
            | d.str.startswith("0.")  # "0.5um and above" form
        )
        sub = sub[node_like]

    if sub.empty:
        return {"metric": metric, "dimensions": [], "periods": [], "rows": []}

    agg = sub.groupby(["dimension", "period_label", "period_end"], as_index=False)["value"].mean()
    period_order = (
        agg[["period_label", "period_end"]]
        .drop_duplicates()
        .sort_values("period_end", ascending=False)
        .head(quarters)
    )
    chosen = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen)]
    piv = agg.pivot(index="dimension", columns="period_label", values="value")
    piv = piv.reindex(columns=chosen)
    # chosen[0] is the most recent period — order dimensions by latest-quarter value desc.
    piv = piv.sort_values(by=chosen[0], ascending=False)
    rows = [
        {
            "dimension": str(idx),
            **{
                q: (None if pd.isna(piv.loc[idx, q]) else float(piv.loc[idx, q]))
                for q in chosen
            },
        }
        for idx in piv.index
    ]
    return {"metric": metric, "periods": chosen, "rows": rows}


@router.get("/capacity")
def capacity(
    quarters: int = Query(28, ge=1, le=200),
    unit: str = Query("kpcs_12in_eq",
                      description="Wafer-equivalent unit. UMC switched from "
                                  "8\" to 12\" reporting in 2024 — pass "
                                  "'kpcs_8in_eq' to see pre-2024 data."),
):
    """Wide-pivot wafer shipments / total capacity / utilization across
    quarters. Wafer + capacity values are filtered by the requested unit;
    utilization is always %."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    METRICS = ["wafer_shipments", "total_capacity", "capacity_utilization", "blended_asp"]
    sub = df[df["metric"].isin(METRICS)].copy()
    if sub.empty:
        return {"ticker": "2303.TW", "unit": unit, "periods": [], "metrics": []}
    # Filter wafer/capacity/ASP to the requested unit family; keep utilization on all units.
    # Wafer/capacity use kpcs_*; ASP uses usd_per_*. Match by suffix:
    asp_unit = unit.replace("kpcs_", "usd_per_")
    keep = (
        (sub["metric"] == "capacity_utilization")
        | ((sub["metric"].isin(["wafer_shipments", "total_capacity"])) & (sub["unit"] == unit))
        | ((sub["metric"] == "blended_asp") & (sub["unit"] == asp_unit))
    )
    sub = sub[keep]
    if sub.empty:
        return {"ticker": "2303.TW", "unit": unit, "periods": [], "metrics": []}

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
    piv = piv.reindex(METRICS).reindex(columns=chosen)

    rows = []
    UNIT_MAP = {
        "wafer_shipments":      unit,
        "total_capacity":       unit,
        "capacity_utilization": "pct",
        "blended_asp":          asp_unit,
    }
    for m in METRICS:
        if m not in piv.index:
            continue
        r = {"metric": m, "unit": UNIT_MAP[m]}
        for q in chosen:
            v = piv.loc[m, q] if q in piv.columns else np.nan
            r[q] = None if pd.isna(v) else float(v)
        rows.append(r)
    return {"ticker": "2303.TW", "unit": unit, "periods": chosen, "metrics": rows}


def _wide_pivot(df: pd.DataFrame, metrics_order: list[str], quarters: int,
                exclude_dimensions_with_prefix: str | None = "annual:") -> dict:
    """Helper: pivot a long-format slice into a wide table with row order
    fixed to `metrics_order`, columns chronological for the most recent
    `quarters` periods. Used by /cashflow, /balance-sheet, /annual."""
    sub = df[df["metric"].isin(metrics_order)].copy()
    if exclude_dimensions_with_prefix:
        sub = sub[~sub["dimension"].astype(str).str.startswith(exclude_dimensions_with_prefix)]
    if sub.empty:
        return {"periods": [], "metrics": []}
    agg = sub.groupby(["metric", "period_label", "period_end"], as_index=False)["value"].mean()
    period_order = (
        agg[["period_label", "period_end"]].drop_duplicates()
        .sort_values("period_end", ascending=False).head(quarters)
    )
    chosen = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen)]
    piv = agg.pivot(index="metric", columns="period_label", values="value")
    piv = piv.reindex(metrics_order).reindex(columns=chosen)
    units = sub.groupby("metric")["unit"].first().to_dict()
    rows = []
    for m in metrics_order:
        if m not in piv.index:
            continue
        r = {"metric": m, "unit": units.get(m, "")}
        for p in chosen:
            v = piv.loc[m, p] if p in piv.columns else np.nan
            r[p] = None if pd.isna(v) else float(v)
        rows.append(r)
    return {"periods": chosen, "metrics": rows}


CASHFLOW_METRICS = [
    "cash_flow_from_operating",
    "depreciation_amortization",
    "income_tax_paid",
    "cash_flow_from_investing",
    "capex_ppe",
    "capex_intangibles",
    "capex_total",
    "free_cash_flow",
    "cash_flow_from_financing",
    "bank_loans_change",
    "bonds_issued",
    "cash_dividends_paid",
    "fx_effect_on_cash",
    "net_cash_flow",
    "cash_beginning_balance",
    "cash_ending_balance",
]


@router.get("/cashflow")
def cashflow(quarters: int = Query(20, ge=1, le=200)):
    """Cash flow statement by quarter. Each report contributes 2 periods
    (curQ + prevQ); overlapping values are averaged across sources."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    out = _wide_pivot(df, CASHFLOW_METRICS, quarters)
    return {"ticker": "2303.TW", **out}


BALANCE_SHEET_METRICS = [
    "cash_and_equivalents",
    "accounts_receivable",
    "days_sales_outstanding",
    "inventories_net",
    "days_of_inventory",
    "total_current_assets",
    "total_current_liabilities",
    "accounts_payable",
    "short_term_debt",
    "equipment_payables",
    "long_term_debt",
    "total_liabilities",
    "debt_to_equity",
    "net_income_before_tax",
]


@router.get("/balance-sheet")
def balance_sheet(quarters: int = Query(20, ge=1, le=200)):
    """Balance sheet highlights by quarter. 3 periods per report (curQ,
    prevQ, 4Q-ago); averaged across overlapping sources."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    out = _wide_pivot(df, BALANCE_SHEET_METRICS, quarters)
    return {"ticker": "2303.TW", **out}


ANNUAL_METRICS = [
    "net_revenue",
    "gross_profit",
    "operating_expenses",
    "other_operating_income",
    "operating_income",
    "non_operating_items",
    "income_tax_expense",
    "net_income",
    "eps",
    "eps_adr",
    "usd_ntd_avg_rate",
]


@router.get("/annual")
def annual(years: int = Query(10, ge=1, le=30)):
    """Full-year P&L. Only Q4 reports contribute (each gives FY-cur and
    FY-prior). Period labels are 'FY25', 'FY24', etc."""
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="quarterly_facts parquet not found")
    df = read_parquet_cached(FACTS_PARQUET)
    # Annual facts are tagged with dimension="annual:{plabel}" so they don't
    # conflict with quarterly facts of the same metric.
    annual_df = df[df["dimension"].astype(str).str.startswith("annual:")]
    if annual_df.empty:
        return {"ticker": "2303.TW", "periods": [], "metrics": []}
    sub = annual_df[annual_df["metric"].isin(ANNUAL_METRICS)].copy()
    agg = sub.groupby(["metric", "period_label", "period_end"], as_index=False)["value"].mean()
    period_order = (
        agg[["period_label", "period_end"]].drop_duplicates()
        .sort_values("period_end", ascending=False).head(years)
    )
    chosen = period_order["period_label"].tolist()  # newest-first per project table convention
    agg = agg[agg["period_label"].isin(chosen)]
    piv = agg.pivot(index="metric", columns="period_label", values="value")
    piv = piv.reindex(ANNUAL_METRICS).reindex(columns=chosen)
    units = sub.groupby("metric")["unit"].first().to_dict()
    rows = []
    for m in ANNUAL_METRICS:
        if m not in piv.index:
            continue
        r = {"metric": m, "unit": units.get(m, "")}
        for p in chosen:
            v = piv.loc[m, p] if p in piv.columns else np.nan
            r[p] = None if pd.isna(v) else float(v)
        rows.append(r)
    return {"ticker": "2303.TW", "periods": chosen, "metrics": rows}


# ---------------------------------------------------------------------------
# Guidance vs Actual
# ---------------------------------------------------------------------------

# Each guidance metric is paired with an "actual" computation strategy.
# - direct: pull a single fact from silver
# - derived: compute from other facts (e.g. gross_margin = gross_profit / net_revenue)
# - qoq_derived: compute QoQ change from current and prior period values
# - n/a: no comparable actual (e.g. 'Will remain firm' is purely directional)

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


def _prev_quarter_label(period_label: str) -> str | None:
    m = re.match(r"(\d)Q(\d{2})", period_label)
    if not m:
        return None
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    prev_total = year * 4 + (q - 1) - 1
    py, pq = divmod(prev_total, 4)
    return f"{pq + 1}Q{str(py)[2:]}"


def _qoq_actual(df: pd.DataFrame, period_label: str, metric: str) -> float | None:
    """Compute curQ % change vs prevQ for a given metric. Returns None if
    either period is missing OR the two periods carry different units (e.g.
    ASP across the 8" → 12" unit shift in 4Q22/1Q23)."""
    cur_rows = df[(df["period_label"] == period_label) & (df["metric"] == metric)
                  & (df["dimension"].isin(["", "chart_estimate"]))]
    if cur_rows.empty:
        return None
    cur_unit = cur_rows["unit"].iloc[0]
    cur_val = float(cur_rows[cur_rows["unit"] == cur_unit]["value"].mean())

    prev_label = _prev_quarter_label(period_label)
    if not prev_label:
        return None
    prev_rows = df[(df["period_label"] == prev_label) & (df["metric"] == metric)
                   & (df["unit"] == cur_unit)
                   & (df["dimension"].isin(["", "chart_estimate"]))]
    if prev_rows.empty:
        return None
    prev_val = float(prev_rows["value"].mean())
    if prev_val == 0:
        return None
    return (cur_val / prev_val - 1.0) * 100.0


def _annual_capex_actual_usd(df: pd.DataFrame, fy_label: str) -> float | None:
    """Sum quarterly capex_total in NTD across the 4 quarters of fy_label,
    then convert to USD billions using the period's average USD/NTD rate.
    Returns None if any of the 4 quarters or the FX rate is missing."""
    fm = re.match(r"FY(\d{2})", fy_label)
    if not fm:
        return None
    yy = int(fm.group(1))
    quarters = [f"{q}Q{yy:02d}" for q in (1, 2, 3, 4)]

    capex_ntd_m = 0.0
    fx_rates: list[float] = []
    for q in quarters:
        c = _actual_for(df, q, "capex_total")
        if c is None:
            return None
        capex_ntd_m += abs(c)
        fx = _actual_for(df, q, "usd_ntd_avg_rate")
        if fx is None:
            return None
        fx_rates.append(fx)

    if not fx_rates:
        return None
    avg_fx = sum(fx_rates) / len(fx_rates)
    # NTD millions / (NTD per USD) -> USD millions, then / 1000 -> USD billions
    return (capex_ntd_m / avg_fx) / 1000.0


def _period_sort_key(label: str) -> tuple[int, int]:
    """Sortable (year, quarter_or_FY-marker) tuple for both 'NQYY' and
    'FYYY' labels. FY uses quarter=5 so it sorts after Q4 of the same year
    when there's a tie (rare; FY guidance is at year level)."""
    fm = re.match(r"FY(\d{2})", label)
    if fm:
        yy = int(fm.group(1))
        year = 2000 + yy if yy < 50 else 1900 + yy
        return (year, 5)
    qm = re.match(r"(\d)Q(\d{2})", label)
    if qm:
        q = int(qm.group(1))
        yy = int(qm.group(2))
        year = 2000 + yy if yy < 50 else 1900 + yy
        return (year, q)
    return (0, 0)


@router.get("/guidance")
def guidance(quarters: int = Query(20, ge=1, le=200)):
    """Forward guidance issued in each report vs the realized actual.
    Includes both the structured numeric range and the verbal-text record.

    Outcome categories (only when guidance has a numeric range):
      BEAT high  — actual > high bound
      MISS low   — actual < low bound
      in range   — actual within [low, high]
      n/a        — verbal-only guidance (no numeric bound to compare)
    """
    if not GUIDANCE_PARQUET.exists():
        raise HTTPException(404, detail="guidance parquet not found")
    if not FACTS_PARQUET.exists():
        raise HTTPException(404, detail="facts parquet not found")
    g_df = read_parquet_cached(GUIDANCE_PARQUET)
    f_df = read_parquet_cached(FACTS_PARQUET)

    # Pivot guidance into a per-(issued, for, metric) record with low/mid/high/point/verbal columns
    keys = ["issued_in_period_label", "for_period_label", "metric"]
    g_df = g_df.sort_values("for_period_label", ascending=False)

    rows: list[dict] = []
    GUIDANCE_TO_ACTUAL = {
        "guidance_gross_margin":         ("derived_gross_margin", None),
        "guidance_capacity_utilization": ("direct",  "capacity_utilization"),
        "guidance_wafer_shipments_qoq":  ("qoq",     "wafer_shipments"),
        "guidance_asp_usd_qoq":          ("qoq",     "blended_asp"),
        "guidance_annual_capex":         ("annual_capex_usd", None),
    }

    for (issued, for_p, metric), grp in g_df.groupby(keys):
        bounds = {r["bound"]: r for _, r in grp.iterrows()}
        verbal = bounds.get("verbal", {}).get("text") if "verbal" in bounds else None
        lo  = bounds.get("low",  {}).get("value") if "low"  in bounds else None
        hi  = bounds.get("high", {}).get("value") if "high" in bounds else None
        mid = bounds.get("midpoint", {}).get("value") if "midpoint" in bounds else None
        pt  = bounds.get("point", {}).get("value") if "point" in bounds else None

        strategy, target = GUIDANCE_TO_ACTUAL.get(metric, ("n/a", None))
        actual = None
        if strategy == "direct" and target:
            actual = _actual_for(f_df, for_p, target)
        elif strategy == "derived_gross_margin":
            actual = _gross_margin_actual(f_df, for_p)
        elif strategy == "qoq" and target:
            actual = _qoq_actual(f_df, for_p, target)
        elif strategy == "annual_capex_usd":
            actual = _annual_capex_actual_usd(f_df, for_p)

        outcome = None
        vs_mid_pct = None
        vs_mid_pp = None
        # Determine the comparison reference: low/high range OR a single point.
        if actual is not None and lo is not None and hi is not None:
            if actual > hi:
                outcome = "BEAT high"
            elif actual < lo:
                outcome = "MISS low"
            else:
                outcome = "in range"
            if mid:
                vs_mid_pp = actual - mid
                if mid != 0:
                    vs_mid_pct = (actual - mid) / mid * 100.0
        elif actual is not None and pt is not None:
            # Point-target metrics (annual_capex): label as BEAT/MISS based
            # on simple comparison (no implicit range). 5% tolerance band
            # qualifies as "in range" since UMC's annual capex guidance is
            # always issued as a point estimate.
            tolerance = abs(pt) * 0.05
            if actual > pt + tolerance:
                outcome = "ABOVE guidance"
            elif actual < pt - tolerance:
                outcome = "BELOW guidance"
            else:
                outcome = "near point"
            vs_mid_pp = actual - pt
            if pt != 0:
                vs_mid_pct = (actual - pt) / pt * 100.0

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

    # Sort by (for_period date desc, metric, issued_in_period date desc).
    # Reverse-chronological within each metric, with FY items sliding in by
    # year. Most-recent quarters at top, regardless of metric.
    rows.sort(key=lambda r: (
        _period_sort_key(r["for_period"]),
        _period_sort_key(r["issued_in_period"]),
        r["metric"],
    ), reverse=True)
    rows = rows[:quarters * 6]  # cap at quarters * (max metrics per quarter)
    return {"ticker": "2303.TW", "rows": rows}


@router.get("/quarters")
def quarters_index():
    """List all distinct (period_label, period_end) pairs we have data for,
    plus the source reports that contributed each. Powers a 'PDF catalog'
    style view in the UI without requiring a separate _index.json."""
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
    return {"ticker": "2303.TW", "quarters": _safe_records(g)}
