"""
data_agent.py — fetches financial metrics from the topline and calculated layers.

Data routing
------------
  1. Calculated layer  (backend/data/filing_data/calculated/ticker=*.parquet)
     Used when pre-built for a ticker. Fastest path; includes YoY/QoQ growth.

  2. Topline layer  (backend/data/filing_data/topline/{statement}/ticker=*.parquet)
     Used when a calculated layer is absent. Clean standalone-quarterly figures
     produced by ToplineBuilder from edgartools XBRL data.

Callers receive the same DataResult regardless of which path was taken.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from .concept_map import (
    BASE_METRIC_CONCEPTS,
    COMPUTED_METRICS,
    TEMPORAL_METRICS,
    resolve_base_dependencies,
)

# ---------------------------------------------------------------------------
# Metric → topline statement routing
# Only base metrics that live in each parquet file need to be here.
# Computed metrics (margins, FCF) are derived in Python after the read.
# ---------------------------------------------------------------------------

_IS_METRICS: frozenset[str] = frozenset([
    "revenue", "cost_of_revenue", "gross_profit", "total_opex",
    "operating_income", "rd_expense", "sga_expense", "pretax_income",
    "income_tax", "net_income", "shares_basic", "shares_diluted",
    "eps_diluted", "eps_basic", "interest_expense", "interest_income",
    "other_income_net",
])

_CF_METRICS: frozenset[str] = frozenset([
    "operating_cf", "investing_cf", "financing_cf", "capex", "depreciation",
])

_BS_METRICS: frozenset[str] = frozenset([
    "cash", "short_term_investments", "long_term_investments",
    "inventories", "accounts_receivable", "ppe_net", "goodwill",
    "intangible_assets", "total_assets", "accounts_payable",
    "total_liabilities", "total_equity", "long_term_debt",
])


# ---------------------------------------------------------------------------
# Public API models
# ---------------------------------------------------------------------------

class DataSpec(BaseModel):
    """What data to fetch."""

    tickers: list[str] = Field(..., description="List of SEC tickers, e.g. ['NVDA', 'AAPL']")
    metrics: list[str] = Field(..., description="Metric names from concept_map.ALL_METRICS")
    period: str = Field("quarterly", description="'quarterly' | 'annual'")
    lookback_years: float = Field(5.0, description="How many years of history to return")

    class Config:
        extra = "forbid"


class DataResult(BaseModel):
    """What DataAgent returns."""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=list)
    metrics_returned: list[str] = Field(default_factory=list)
    # "calculated_layer" | "topline" | "mixed" | "none"
    source: str = "topline"
    sql_executed: str = ""   # kept for API contract stability; always empty in topline path
    warnings: list[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


# ---------------------------------------------------------------------------
# DataAgent
# ---------------------------------------------------------------------------

class DataAgent:
    """
    Fetches, derives, and returns financial metrics.

    Routing priority per ticker:
      1. Calculated layer — richer (includes YoY/QoQ); used when available.
      2. Topline layer    — clean XBRL quarterly data; used as fallback.
      Neither available   — ticker skipped with a warning.
    """

    def fetch(self, spec: DataSpec) -> DataResult:
        warn_list: list[str] = []

        # ------------------------------------------------------------------
        # 1. Validate requested metrics
        # ------------------------------------------------------------------
        all_known = set(BASE_METRIC_CONCEPTS) | set(COMPUTED_METRICS) | set(TEMPORAL_METRICS)
        unknown = [m for m in spec.metrics if m not in all_known]
        if unknown:
            warn_list.append(f"Unknown metrics ignored: {unknown}")

        valid_metrics = [m for m in spec.metrics if m not in unknown]
        if not valid_metrics:
            return DataResult(warnings=warn_list + ["No valid metrics to fetch."])

        has_temporal = any(m in TEMPORAL_METRICS for m in valid_metrics)

        # ------------------------------------------------------------------
        # 2. Route per ticker
        # ------------------------------------------------------------------
        from .calculator import CalculatedLayerBuilder   # lazy to avoid circular import
        from .topline_builder import ToplineBuilder

        calc_builder    = CalculatedLayerBuilder()
        topline_builder = ToplineBuilder()

        calc_tickers    = [t for t in spec.tickers if calc_builder.is_available(t)]
        topline_tickers = [t for t in spec.tickers
                           if t not in calc_tickers and topline_builder.is_available(t)]
        missing_tickers = [t for t in spec.tickers
                           if t not in calc_tickers and t not in topline_tickers]

        if missing_tickers:
            warn_list.append(
                f"No topline or calculated data for: {missing_tickers}. "
                "Run ToplineBuilder().build() to generate it."
            )

        if has_temporal and topline_tickers:
            warn_list.append(
                f"YoY/QoQ metrics not available via topline path for {topline_tickers} — "
                "calculated layer not built for those tickers. "
                "Run CalculatedLayerBuilder().build() to enable growth metrics."
            )

        frames: list[pd.DataFrame] = []
        sources_used: set[str] = set()

        # ------------------------------------------------------------------
        # 3a. Calculated layer path
        # ------------------------------------------------------------------
        if calc_tickers:
            try:
                calc_df = calc_builder.read(
                    tickers=calc_tickers,
                    metrics=valid_metrics,
                    lookback_years=spec.lookback_years,
                    period=spec.period,
                )
                if not calc_df.empty:
                    frames.append(calc_df)
                    sources_used.add("calculated_layer")
            except Exception as exc:
                warn_list.append(
                    f"Calculated layer read failed ({exc}). Falling back to topline."
                )
                topline_tickers = list(set(topline_tickers + calc_tickers))

        # ------------------------------------------------------------------
        # 3b. Topline layer path
        # ------------------------------------------------------------------
        non_temporal_metrics = [m for m in valid_metrics if m not in TEMPORAL_METRICS]

        if topline_tickers and non_temporal_metrics:
            try:
                topline_df = self._fetch_from_topline(
                    topline_builder,
                    topline_tickers,
                    non_temporal_metrics,
                    spec.period,
                    spec.lookback_years,
                )
                if not topline_df.empty:
                    topline_df, derived_warns = self._compute_derived(
                        topline_df, non_temporal_metrics
                    )
                    warn_list.extend(derived_warns)
                    frames.append(topline_df)
                    sources_used.add("topline")
            except Exception as exc:
                warn_list.append(f"Topline read failed: {exc}")

        # ------------------------------------------------------------------
        # 4. Merge and format output
        # ------------------------------------------------------------------
        if not frames:
            return DataResult(warnings=warn_list + ["No data returned."])

        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["ticker", "end_date"])

        # Build period_label: use fiscal quarter/year from topline when present;
        # otherwise fall back to calendar-quarter label from end_date.
        df["period_label"] = df.apply(self._make_period_label, axis=1)

        final_metrics = [m for m in valid_metrics if m in df.columns]
        output_cols   = ["ticker", "period_label", "end_date"] + final_metrics
        output_cols   = [c for c in output_cols if c in df.columns]

        rows = df[output_cols].replace({np.nan: None}).to_dict(orient="records")

        source = (
            "mixed" if len(sources_used) > 1
            else next(iter(sources_used), "none")
        )

        return DataResult(
            rows=rows,
            tickers=sorted(df["ticker"].unique().tolist()),
            periods=df["period_label"].unique().tolist(),
            metrics_returned=final_metrics,
            source=source,
            warnings=warn_list,
        )

    # ------------------------------------------------------------------
    # Topline read
    # ------------------------------------------------------------------

    def _fetch_from_topline(
        self,
        builder,
        tickers: list[str],
        metrics: list[str],
        period: str,
        lookback_years: float,
    ) -> pd.DataFrame:
        """
        Read clean quarterly/annual data from topline/ parquets.

        Uses income_statement as the row spine. Cash-flow and balance-sheet
        columns are left-joined on (ticker, period_end) when the requested
        metrics require them.

        Returns DataFrame with end_date (renamed from period_end), ticker,
        fiscal_quarter, fiscal_year, and the available metric columns.
        """
        # Determine which statement files are needed
        base_needed: set[str] = set(resolve_base_dependencies(metrics)) | (
            set(metrics) & set(BASE_METRIC_CONCEPTS)
        )
        needs_cf = bool(base_needed & _CF_METRICS)
        needs_bs = bool(base_needed & _BS_METRICS)

        quarter_filter = ["Q1", "Q2", "Q3", "Q4"] if period == "quarterly" else ["Annual"]

        frames: list[pd.DataFrame] = []
        for ticker in tickers:
            # Income statement is the primary spine
            try:
                is_df = builder.read(ticker, "income_statement", lookback_years)
            except FileNotFoundError:
                continue

            is_df = is_df[is_df["fiscal_quarter"].isin(quarter_filter)]
            if is_df.empty:
                continue

            df = is_df.copy()

            if needs_cf:
                try:
                    cf_df = builder.read(ticker, "cash_flow", lookback_years)
                    cf_df = cf_df[cf_df["fiscal_quarter"].isin(quarter_filter)]
                    cf_cols = [c for c in cf_df.columns if c in _CF_METRICS]
                    if cf_cols:
                        df = df.merge(
                            cf_df[["ticker", "period_end"] + cf_cols],
                            on=["ticker", "period_end"],
                            how="left",
                        )
                except FileNotFoundError:
                    pass

            if needs_bs:
                try:
                    bs_df = builder.read(ticker, "balance_sheet", lookback_years)
                    bs_cols = [c for c in bs_df.columns if c in _BS_METRICS]
                    if bs_cols:
                        # Balance sheet joins on period_end only (no fiscal_quarter filter:
                        # instant dates may not carry quarter labels)
                        df = df.merge(
                            bs_df[["ticker", "period_end"] + bs_cols],
                            on=["ticker", "period_end"],
                            how="left",
                        )
                except FileNotFoundError:
                    pass

            frames.append(df)

        if not frames:
            return pd.DataFrame()

        merged = pd.concat(frames, ignore_index=True)
        # Rename topline column names → DataResult-compatible names
        merged = merged.rename(columns={
            "period_end":   "end_date",
            "period_start": "start_date",
        })
        return merged

    # ------------------------------------------------------------------
    # Derived metric computation (margins, FCF, R&D%)
    # ------------------------------------------------------------------

    def _compute_derived(
        self,
        df: pd.DataFrame,
        requested_metrics: list[str],
    ) -> tuple[pd.DataFrame, list[str]]:
        warns: list[str] = []
        for metric in requested_metrics:
            if metric not in COMPUTED_METRICS:
                continue
            spec = COMPUTED_METRICS[metric]
            deps = spec["requires"]
            missing = [d for d in deps if d not in df.columns]
            if missing:
                warns.append(f"Cannot compute {metric}: missing base metrics {missing}")
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    df[metric] = df.apply(
                        lambda row, deps=deps, f=spec["formula"]: self._safe_apply(f, row, deps),
                        axis=1,
                    )
            except Exception as exc:
                warns.append(f"Error computing {metric}: {exc}")
        return df, warns

    @staticmethod
    def _safe_apply(formula, row: pd.Series, deps: list[str]):
        d = {dep: row[dep] for dep in deps}
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in d.values()):
            return None
        if d.get("revenue", 1) == 0:
            return None
        try:
            return formula(d)
        except (ZeroDivisionError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Period label
    # ------------------------------------------------------------------

    @staticmethod
    def _make_period_label(row: pd.Series) -> str:
        """
        Build a fiscal-accurate period label when topline data provides
        fiscal_quarter + fiscal_year (e.g. 'FY2025-Q3' for NVDA's Oct quarter).
        Falls back to calendar-quarter label for calculated-layer rows that
        don't carry those columns.
        """
        fq = row.get("fiscal_quarter")
        fy = row.get("fiscal_year")
        if (
            fq and fq not in ("Unknown", "Instant", "Annual")
            and fy is not None
            and not (isinstance(fy, float) and np.isnan(fy))
        ):
            return f"FY{int(fy)}-{fq}"
        if fq == "Annual" and fy is not None and not (isinstance(fy, float) and np.isnan(fy)):
            return f"FY{int(fy)}-Annual"
        # Fallback: infer from end_date calendar month
        try:
            d = pd.Timestamp(row["end_date"])
            q = (d.month - 1) // 3 + 1
            return f"{d.year}-Q{q}"
        except Exception:
            return str(row.get("end_date", ""))
