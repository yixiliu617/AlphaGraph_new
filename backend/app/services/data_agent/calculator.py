"""
calculator.py — builds and reads the Calculated Data Layer.

Separation of concerns
-----------------------
  backbone/     RAW SEC parquet data.  Never written here.
  calculated/   Pre-computed wide-format parquet, one file per ticker.
                Safe to delete and rebuild at any time.

What gets pre-computed
-----------------------
  - All base metrics (already deduplicated + scaled to millions by DataAgent SQL)
  - Computed metrics: margins, free cash flow, R&D % revenue
  - YoY % for GROWTH_BASE_METRICS  (compares same fiscal quarter prior year)
  - QoQ % for GROWTH_BASE_METRICS  (compares prior sequential quarter)

YoY/QoQ date safety
--------------------
  We shift by 4 rows (YoY) or 1 row (QoQ) within each ticker, then verify the
  shifted end_date is within 45 days of the expected offset. If the gap is wrong
  (missing quarter, data gap) the growth figure is set to NaN rather than
  silently comparing the wrong periods.

Output schema (wide-format parquet)
-------------------------------------
  ticker, end_date, start_date, period_type,
  revenue, gross_profit, ...,             # base metrics (millions)
  gross_margin_pct, net_margin_pct, ...,  # computed
  revenue_yoy_pct, revenue_qoq_pct, ...  # temporal

Validation
-----------
  After building each ticker, _validate() runs sanity checks and records any
  failures. A _build_report.json is written to calculated/ summarising every
  ticker's row count, warnings, and spot-check results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .concept_map import (
    BASE_METRIC_CONCEPTS,
    COMPUTED_METRICS,
    CONSOLIDATION_DIM_COLS,
    GROWTH_BASE_METRICS,
    MARGIN_DELTA_BASE_METRICS,
    PERIOD_WINDOWS,
    RAW_SCALE_METRICS,
    TEMPORAL_METRICS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]
_BACKBONE_DIR = _REPO_ROOT / "backend" / "data" / "filing_data" / "backbone"
_CALCULATED_DIR = _REPO_ROOT / "backend" / "data" / "filing_data" / "calculated"
_BUILD_REPORT = _CALCULATED_DIR / "_build_report.json"

# ---------------------------------------------------------------------------
# Date tolerance for YoY/QoQ validation
# ---------------------------------------------------------------------------
_YOY_SHIFT = 4          # quarters
_YOY_TOLERANCE_DAYS = 45
_QOQ_SHIFT = 1
_QOQ_TOLERANCE_DAYS = 45

# ---------------------------------------------------------------------------
# Known spot-check values used to validate computation correctness.
# Each entry: (ticker, end_date_str, metric, expected_value, tolerance_pct)
# ---------------------------------------------------------------------------
_SPOT_CHECKS: list[tuple[str, str, str, float, float]] = [
    # NVDA Q4 FY24 (end Oct 2024) — from verified DataAgent output
    ("NVDA", "2024-10-27", "revenue",          35082.0,  1.0),
    ("NVDA", "2024-10-27", "gross_margin_pct",    74.56,  0.5),
    ("NVDA", "2024-10-27", "net_income",        19309.0,  1.0),
    ("NVDA", "2024-10-27", "eps_diluted",           0.78, 2.0),
    # NVDA Q3 FY25 (end Jul 2025)
    ("NVDA", "2025-07-27", "revenue",          46743.0,  1.0),
    ("NVDA", "2025-07-27", "gross_margin_pct",    72.42,  0.5),
]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class CalculatedLayerBuilder:
    """
    Builds, validates, and reads the calculated data layer.

    Usage:
        builder = CalculatedLayerBuilder()
        report  = builder.build()          # (re)compute all tickers
        df      = builder.read(["NVDA"])   # read back
    """

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, tickers: list[str] | None = None) -> dict[str, Any]:
        """
        Build the calculated layer for the given tickers (all if None).
        Returns a per-ticker build report dict.
        """
        _CALCULATED_DIR.mkdir(parents=True, exist_ok=True)

        available = self._discover_tickers()
        if tickers is None:
            tickers = available
        else:
            missing = [t for t in tickers if t not in available]
            if missing:
                log.warning("No backbone parquet for tickers: %s", missing)
            tickers = [t for t in tickers if t in available]

        report: dict[str, Any] = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "tickers": {},
        }

        for ticker in tickers:
            log.info("Building calculated layer for %s ...", ticker)
            ticker_report: dict[str, Any] = {}
            try:
                raw_df = self._fetch_raw_wide(ticker)
                wide_df = self._compute_all(raw_df, ticker)
                validation = self._validate(wide_df, ticker)
                ticker_report["rows"] = len(wide_df)
                ticker_report["columns"] = list(wide_df.columns)
                ticker_report["validation"] = validation
                self._write_ticker(wide_df, ticker)
                log.info("  %s: %d rows, %d warnings", ticker, len(wide_df), len(validation.get("warnings", [])))
            except Exception as exc:
                ticker_report["error"] = str(exc)
                log.error("  %s FAILED: %s", ticker, exc)

            report["tickers"][ticker] = ticker_report

        _BUILD_REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        log.info("Build complete. Report written to %s", _BUILD_REPORT)
        return report

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(
        self,
        tickers: list[str],
        metrics: list[str] | None = None,
        lookback_years: float = 5.0,
        period: str = "quarterly",
    ) -> pd.DataFrame:
        """
        Read pre-computed data for the given tickers from the calculated layer.
        Returns a wide-format DataFrame with: ticker, end_date, start_date,
        fiscal_quarter (when present), fiscal_year (when present), <metrics...>
        Raises FileNotFoundError if a ticker's calculated parquet doesn't exist.
        """
        frames: list[pd.DataFrame] = []
        for ticker in tickers:
            path = _CALCULATED_DIR / f"ticker={ticker}.parquet"
            if not path.exists():
                raise FileNotFoundError(
                    f"Calculated layer missing for {ticker}. "
                    f"Run CalculatedLayerBuilder().build(['{ticker}']) first."
                )
            frames.append(pd.read_parquet(path))

        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)

        # ── Period type filter ───────────────────────────────────────────────
        # Prefer fiscal_quarter column (accurate for non-December FY companies).
        # Fall back to duration-based filter for legacy backbone-built parquets.
        if "fiscal_quarter" in df.columns:
            if period == "quarterly":
                df = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])]
            elif period in ("annual", "ttm"):
                df = df[df["fiscal_quarter"] == "Annual"]
        elif "start_date" in df.columns:
            dur_min, dur_max = PERIOD_WINDOWS.get(period, PERIOD_WINDOWS["quarterly"])
            df["_dur"] = (df["end_date"] - df["start_date"]).dt.days
            df = df[(df["_dur"] >= dur_min) & (df["_dur"] <= dur_max)].drop(columns=["_dur"])

        # ── Lookback filter ─────────────────────────────────────────────────
        cutoff = pd.Timestamp.now() - pd.DateOffset(years=lookback_years)
        df = df[df["end_date"] >= cutoff]

        # ── Column selection ────────────────────────────────────────────────
        # Always include fiscal_quarter / fiscal_year so DataAgent can build
        # accurate fiscal period labels (e.g. FY2025-Q3 for NVDA).
        if metrics:
            id_cols = ["ticker", "end_date"]
            for extra in ("start_date", "fiscal_quarter", "fiscal_year"):
                if extra in df.columns:
                    id_cols.append(extra)
            metric_cols = [m for m in metrics if m in df.columns]
            df = df[id_cols + metric_cols]

        return df.sort_values(["ticker", "end_date"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return the last build report, or indicate no build has been run."""
        if not _BUILD_REPORT.exists():
            return {"built": False}
        report = json.loads(_BUILD_REPORT.read_text(encoding="utf-8"))
        report["built"] = True
        return report

    def is_available(self, ticker: str) -> bool:
        return (_CALCULATED_DIR / f"ticker={ticker}.parquet").exists()

    # ------------------------------------------------------------------
    # Internal: fetch raw and pivot wide
    # ------------------------------------------------------------------

    def _discover_tickers(self) -> list[str]:
        # Prefer topline layer (the clean authoritative source)
        topline_is_dir = _REPO_ROOT / "backend" / "data" / "filing_data" / "topline" / "income_statement"
        if topline_is_dir.exists():
            tickers = [p.stem.replace("ticker=", "") for p in topline_is_dir.glob("ticker=*.parquet")]
            if tickers:
                return tickers
        # Fallback: backbone parquets (legacy path)
        return [p.stem.replace("ticker=", "") for p in _BACKBONE_DIR.glob("ticker=*.parquet")]

    def _fetch_raw_wide(self, ticker: str) -> pd.DataFrame:
        """
        Pull all base metrics for a single ticker from topline parquets.

        Reads income_statement (primary spine) + cash_flow, merges on period_end,
        and returns quarterly rows only (Q1-Q4).  Annual rows are excluded to
        keep consecutive-row YoY/QoQ shift logic clean — annual growth rates are
        not pre-computed in the calculated layer.

        Falls back to backbone DuckDB if topline data is absent.
        """
        from .topline_builder import ToplineBuilder
        builder = ToplineBuilder()

        if not builder.is_available(ticker):
            return self._fetch_raw_wide_backbone(ticker)

        # ── Income statement ──────────────────────────────────────────
        try:
            is_df = builder.read(ticker, "income_statement", lookback_years=10.0)
        except FileNotFoundError:
            return self._fetch_raw_wide_backbone(ticker)

        # Quarterly standalone rows only. Require fiscal_quarter in Q1-Q4 AND
        # is_ytd=False. Topline keeps rows as is_ytd=True when a YTD value
        # couldn't be converted to standalone (e.g. DELL missing Q1/H1
        # comparative columns from edgartools), so they must be dropped here.
        is_df = is_df[
            is_df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
            & (~is_df["is_ytd"].astype(bool))
        ]

        if is_df.empty:
            return pd.DataFrame()

        df = is_df.copy()

        # ── Cash flow ─────────────────────────────────────────────────
        try:
            cf_df = builder.read(ticker, "cash_flow", lookback_years=10.0)
            cf_df = cf_df[
                cf_df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
                & (~cf_df["is_ytd"].astype(bool))
            ]
            cf_cols = [c for c in cf_df.columns
                       if c in ("operating_cf", "investing_cf", "financing_cf", "capex", "depreciation")]
            if cf_cols:
                df = df.merge(
                    cf_df[["ticker", "period_end"] + cf_cols],
                    on=["ticker", "period_end"],
                    how="left",
                )
        except FileNotFoundError:
            pass

        # Rename topline columns → calculator-compatible names
        df = df.rename(columns={"period_end": "end_date", "period_start": "start_date"})
        return df.sort_values("end_date").reset_index(drop=True)

    def _fetch_raw_wide_backbone(self, ticker: str) -> pd.DataFrame:
        """
        Legacy backbone path — kept as fallback when topline is not yet built.
        Reads raw XBRL facts from backbone/ via DuckDB and pivots to wide format.
        """
        backbone_path = str(_BACKBONE_DIR / f"ticker={ticker}.parquet")
        if not (_BACKBONE_DIR / f"ticker={ticker}.parquet").exists():
            return pd.DataFrame()

        all_concepts: list[str] = []
        seen: set[str] = set()
        for concepts in BASE_METRIC_CONCEPTS.values():
            for c in concepts:
                if c not in seen:
                    seen.add(c)
                    all_concepts.append(c)
        concept_list = ", ".join(f"'{c}'" for c in all_concepts)

        existing_cols: set[str] = set(
            duckdb.connect(":memory:")
            .execute(f"DESCRIBE SELECT * FROM read_parquet('{backbone_path}') LIMIT 0")
            .df()["column_name"]
            .tolist()
        )
        active_dim_cols = [c for c in CONSOLIDATION_DIM_COLS if c in existing_cols]
        dim_filters = (
            "\n        AND ".join(f'("{col}" IS NULL OR "{col}" = \'\')' for col in active_dim_cols)
            if active_dim_cols else "1=1"
        )

        sql = f"""
WITH filtered AS (
    SELECT
        CAST(end_date   AS DATE) AS end_date,
        CAST(start_date AS DATE) AS start_date,
        concept, value,
        RANK() OVER (
            PARTITION BY CAST(end_date AS DATE), concept
            ORDER BY filing_date DESC
        ) AS _filing_rank
    FROM read_parquet('{backbone_path}')
    WHERE concept IN ({concept_list})
      AND {dim_filters}
      AND (
            DATEDIFF('day', CAST(start_date AS DATE), CAST(end_date AS DATE)) BETWEEN 80  AND 100
         OR DATEDIFF('day', CAST(start_date AS DATE), CAST(end_date AS DATE)) BETWEEN 340 AND 380
      )
),
deduped AS (
    SELECT end_date, start_date, concept, MAX(value) AS value
    FROM filtered WHERE _filing_rank = 1
    GROUP BY end_date, start_date, concept
)
SELECT end_date, start_date, concept, value FROM deduped ORDER BY end_date
"""
        con = duckdb.connect(":memory:")
        df  = con.execute(sql).df()
        con.close()

        if df.empty:
            return pd.DataFrame()

        wide_rows: list[dict] = []
        for (end_date, start_date), grp in df.groupby(["end_date", "start_date"]):
            concept_vals: dict[str, float] = dict(zip(grp["concept"], grp["value"]))
            row: dict = {
                "ticker":     ticker,
                "end_date":   pd.Timestamp(end_date),
                "start_date": pd.Timestamp(start_date),
            }
            for metric, concepts in BASE_METRIC_CONCEPTS.items():
                val = None
                for c in concepts:
                    v = concept_vals.get(c)
                    if v is not None and not (isinstance(v, float) and np.isnan(v)):
                        val = v
                        break
                if val is not None:
                    row[metric] = round(val / 1_000_000, 4) if metric not in RAW_SCALE_METRICS else round(val, 6)
                else:
                    row[metric] = None
            wide_rows.append(row)

        return pd.DataFrame(wide_rows)

    # ------------------------------------------------------------------
    # Internal: compute all derived metrics
    # ------------------------------------------------------------------

    def _compute_all(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if df.empty:
            return df

        df = df.sort_values("end_date").reset_index(drop=True)

        # Step 1: COMPUTED_METRICS (margins, FCF, R&D%)
        for metric, spec in COMPUTED_METRICS.items():
            deps = spec["requires"]
            if all(d in df.columns for d in deps):
                df[metric] = df.apply(
                    lambda row, deps=deps, formula=spec["formula"]: self._safe_formula(formula, row, deps),
                    axis=1,
                )

        # Note: we previously had a "cumulative-YTD leak guard" here that
        # dropped rows with revenue > Nx median or neighbors. It was REMOVED
        # because it kept breaking legitimate quarters for growing companies
        # (NVDA's revenue went from $7B to $68B; LITE from $300M to $666M).
        # Cumulative leaks should be fixed at the TOPLINE LAYER (period_map
        # classification + is_ytd flag), not by post-hoc revenue magnitude
        # checks in the calculator. See skill: edgar-topline-extraction.

        # Step 2: YoY and QoQ for GROWTH_BASE_METRICS
        # free_cash_flow is a computed metric but we grow it too
        growth_candidates = [m for m in GROWTH_BASE_METRICS if m in df.columns]

        df = self._add_growth(df, growth_candidates, shift=_YOY_SHIFT,
                              tolerance_days=_YOY_TOLERANCE_DAYS, suffix="_yoy_pct",
                              expected_days=365)
        df = self._add_growth(df, growth_candidates, shift=_QOQ_SHIFT,
                              tolerance_days=_QOQ_TOLERANCE_DAYS, suffix="_qoq_pct",
                              expected_days=91)

        # Step 3: Margin deltas — YoY absolute percentage-point difference
        # on the margin metrics.  Unlike _yoy_pct (a growth rate), these are
        # a simple subtraction: current_margin - same_margin_4q_ago, in pp.
        margin_delta_candidates = [m for m in MARGIN_DELTA_BASE_METRICS if m in df.columns]
        df = self._add_delta(df, margin_delta_candidates, shift=_YOY_SHIFT,
                             tolerance_days=_YOY_TOLERANCE_DAYS, suffix="_diff_yoy",
                             expected_days=365)

        return df

    def _find_prior_rows(
        self,
        df: pd.DataFrame,
        expected_days: int,
        tolerance_days: int,
    ) -> pd.DataFrame:
        """
        For each row, find the row whose end_date is closest to (current - expected_days).
        Returns a DataFrame aligned to df.index with the prior row's end_date
        and a boolean column indicating whether the match is within tolerance.

        Uses merge_asof, which is O(n log n) and robust to gaps in the time
        series. A missing prior-year quarter no longer cascades into broken
        YoY for the next several quarters — each row finds its own match
        independently.
        """
        if df.empty:
            return pd.DataFrame(columns=["prior_end_date", "prior_idx", "valid_gap"])

        left = df[["end_date"]].copy()
        left["_left_idx"] = left.index
        left = left.sort_values("end_date").reset_index(drop=True)
        left["_target_date"] = left["end_date"] - pd.Timedelta(days=expected_days)

        right = df[["end_date"]].copy()
        right["_right_idx"] = right.index
        right = right.sort_values("end_date").reset_index(drop=True)

        matched = pd.merge_asof(
            left.sort_values("_target_date"),
            right.rename(columns={"end_date": "prior_end_date"}),
            left_on="_target_date",
            right_on="prior_end_date",
            direction="nearest",
            tolerance=pd.Timedelta(days=tolerance_days),
        )
        matched = matched.set_index("_left_idx")
        return matched[["prior_end_date", "_right_idx"]].rename(columns={"_right_idx": "prior_idx"})

    def _add_delta(
        self,
        df: pd.DataFrame,
        metrics: list[str],
        shift: int,
        tolerance_days: int,
        suffix: str,
        expected_days: int,
    ) -> pd.DataFrame:
        """
        Percentage-point delta vs the row whose end_date is closest to
        (current - expected_days), within tolerance_days.

        Uses end_date-based matching (via merge_asof) rather than row-shift,
        so missing prior-year quarters don't cascade into broken deltas for
        unrelated subsequent quarters.
        """
        if not metrics or df.empty:
            return df

        lookup = self._find_prior_rows(df, expected_days, tolerance_days)

        for metric in metrics:
            prior_vals = pd.Series(index=df.index, dtype="float64")
            for idx, row in lookup.iterrows():
                pi = row["prior_idx"]
                if pd.notna(pi):
                    prior_vals.at[idx] = df.at[pi, metric]
            delta = (df[metric] - prior_vals).round(2)
            df[f"{metric}{suffix}"] = delta

        return df

    def _add_growth(
        self,
        df: pd.DataFrame,
        metrics: list[str],
        shift: int,
        tolerance_days: int,
        suffix: str,
        expected_days: int,
    ) -> pd.DataFrame:
        """
        Percent change vs the row whose end_date is closest to
        (current - expected_days), within tolerance_days.

        End-date-based matching (via merge_asof) rather than row-shift. This
        means a gap at, say, FY2024 Q3 no longer breaks YoY for FY2025 Q4 —
        each row matches against its own calendar-appropriate prior row
        independently.
        """
        if not metrics or df.empty:
            return df

        lookup = self._find_prior_rows(df, expected_days, tolerance_days)

        for metric in metrics:
            prior_vals = pd.Series(index=df.index, dtype="float64")
            for idx, row in lookup.iterrows():
                pi = row["prior_idx"]
                if pd.notna(pi):
                    prior_vals.at[idx] = df.at[pi, metric]
            pct = ((df[metric] - prior_vals) / prior_vals.abs() * 100).round(2)
            df[f"{metric}{suffix}"] = pct

        return df

    @staticmethod
    def _safe_formula(formula, row: pd.Series, deps: list[str]):
        d = {dep: row[dep] for dep in deps}
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in d.values()):
            return np.nan   # np.nan keeps column dtype numeric; None would cause object dtype
        if d.get("revenue", 1) == 0:
            return np.nan
        try:
            result = formula(d)
            return result if result is not None else np.nan
        except (ZeroDivisionError, TypeError):
            return np.nan

    # ------------------------------------------------------------------
    # Internal: write
    # ------------------------------------------------------------------

    def _write_ticker(self, df: pd.DataFrame, ticker: str) -> None:
        out_path = _CALCULATED_DIR / f"ticker={ticker}.parquet"
        df.to_parquet(out_path, index=False, engine="pyarrow")

    # ------------------------------------------------------------------
    # Internal: validate
    # ------------------------------------------------------------------

    def _validate(self, df: pd.DataFrame, ticker: str) -> dict[str, Any]:
        """
        Run sanity checks on the computed DataFrame.

        Structural invariants (sign, range, cross-metric, cliff detection) live
        in the data_quality rules framework — add new checks by appending to
        data_quality/rules.py, not this function.

        This method still owns:
          - Empty-frame guard
          - Spot checks against known values
          - Legacy YoY/QoQ extreme-growth warning

        Returns a dict with "warnings", "spot_checks", "quality_report".
        """
        from .data_quality import run_rules

        warnings_: list[str] = []
        spot_results: list[dict] = []

        if df.empty:
            warnings_.append("DataFrame is empty after computation.")
            return {"warnings": warnings_, "spot_checks": spot_results, "quality_report": None}

        # ── Run the data quality invariants framework ──────────────────────
        # All sign / range / cross-metric / cliff rules live in data_quality/rules.py.
        # Violations already filtered by KNOWN_EXCEPTIONS (e.g. NVDA stock split).
        quality_report = run_rules(df, ticker)
        warnings_.extend(quality_report.warning_messages())

        # ── YoY/QoQ extreme-growth warning (not yet in rules framework) ───
        for col in df.columns:
            if not col.endswith("_yoy_pct") and not col.endswith("_qoq_pct"):
                continue
            series = df[col].dropna()
            if series.empty:
                continue
            extreme = (series.abs() > 1000).sum()
            if extreme > 0:
                warnings_.append(
                    f"{col}: {extreme} rows with |growth| > 1000% -- may indicate data anomaly"
                )

        # ── Coverage check: revenue rows must have YoY/QoQ ──────────────────
        # When revenue exists, the matching YoY% and QoQ% should also exist
        # for every row except the first (no prior-year) and second (no prior-Q)
        # rows of a ticker's series. Surfaces gaps in the data so we can find
        # missing prior-year rows that would otherwise silently produce blanks.
        if "revenue" in df.columns:
            std = df[
                df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
                & (~df["is_ytd"].astype(bool))
                & df["revenue"].notna()
            ].sort_values("end_date").reset_index(drop=True)

            for col, expected_skip in [
                ("revenue_yoy_pct", 4),  # first 4 rows have no prior-year match
                ("revenue_qoq_pct", 1),  # first row has no prior quarter
            ]:
                if col not in std.columns:
                    continue
                gap_rows = std.iloc[expected_skip:][std.iloc[expected_skip:][col].isna()]
                if not gap_rows.empty:
                    end_dates = ", ".join(
                        gap_rows["end_date"].dt.strftime("%Y-%m-%d").tolist()[:5]
                    )
                    extra = "" if len(gap_rows) <= 5 else f" (+{len(gap_rows)-5} more)"
                    warnings_.append(
                        f"{ticker}: revenue present but {col} is NaN for "
                        f"{len(gap_rows)} row(s): {end_dates}{extra} — likely "
                        f"a missing or mislabeled prior-period row in topline."
                    )

        # ──────────────────────────────────────────────────────────────────
        # Quality sanity checks (added per user request)
        # ──────────────────────────────────────────────────────────────────
        std_q = df[
            df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
            & (~df["is_ytd"].astype(bool))
        ].copy()
        std_q = std_q.sort_values("end_date").reset_index(drop=True)

        def _fy_label(row) -> str:
            try:
                fy = int(row["fiscal_year"])
                fq = row["fiscal_quarter"]
                return f"FY{fy}-{fq}"
            except Exception:
                return str(row.get("end_date", ""))[:10]

        def _add(severity: str, category: str, msg: str) -> None:
            warnings_.append(f"[{severity.upper()}/{category}] {msg}")

        # ── HARD #1: revenue must be > 0 ────────────────────────────────
        if "revenue" in std_q.columns:
            bad = std_q[std_q["revenue"] <= 0]
            for _, r in bad.iterrows():
                _add("hard", "sign", f"{ticker} {_fy_label(r)}: revenue = {r['revenue']} (must be > 0)")

        # ── HARD #2: cost_of_revenue must be > 0 when present ──────────
        if "cost_of_revenue" in std_q.columns:
            bad = std_q[std_q["cost_of_revenue"].notna() & (std_q["cost_of_revenue"] <= 0)]
            for _, r in bad.iterrows():
                _add("hard", "sign", f"{ticker} {_fy_label(r)}: cost_of_revenue = {r['cost_of_revenue']} (must be > 0)")

        # ── HARD #3: opex must be > 0 when present ─────────────────────
        if "opex" in std_q.columns:
            bad = std_q[std_q["opex"].notna() & (std_q["opex"] <= 0)]
            for _, r in bad.iterrows():
                _add("hard", "sign", f"{ticker} {_fy_label(r)}: opex = {r['opex']} (must be > 0)")

        # ── HARD #5: rd_expense and sga_expense must be ≥ 0 when present ──
        for col in ("rd_expense", "sga_expense"):
            if col in std_q.columns:
                bad = std_q[std_q[col].notna() & (std_q[col] < 0)]
                for _, r in bad.iterrows():
                    _add("hard", "sign", f"{ticker} {_fy_label(r)}: {col} = {r[col]} (must be ≥ 0)")

        # ── HARD #6: shares_basic and shares_diluted must be > 0 when present ──
        for col in ("shares_basic", "shares_diluted"):
            if col in std_q.columns:
                bad = std_q[std_q[col].notna() & (std_q[col] <= 0)]
                for _, r in bad.iterrows():
                    _add("hard", "sign", f"{ticker} {_fy_label(r)}: {col} = {r[col]} (must be > 0)")

        # ── HARD #7: revenue − cost_of_revenue ≈ gross_profit (within 1% of revenue) ──
        if all(c in std_q.columns for c in ("revenue", "cost_of_revenue", "gross_profit")):
            sub = std_q.dropna(subset=["revenue", "cost_of_revenue", "gross_profit"])
            for _, r in sub.iterrows():
                expected = r["revenue"] - r["cost_of_revenue"]
                actual   = r["gross_profit"]
                tol      = abs(r["revenue"]) * 0.01
                if abs(expected - actual) > tol:
                    _add(
                        "hard", "identity",
                        f"{ticker} {_fy_label(r)}: revenue ({r['revenue']:.0f}) − cost_of_revenue "
                        f"({r['cost_of_revenue']:.0f}) = {expected:.0f}; gross_profit reported = "
                        f"{actual:.0f} (Δ {abs(expected-actual):.0f}, > 1% of revenue)"
                    )

        # ── HARD #8: gross_profit − opex ≈ operating_income (within 1% of revenue) ──
        if all(c in std_q.columns for c in ("revenue", "gross_profit", "opex", "operating_income")):
            sub = std_q.dropna(subset=["revenue", "gross_profit", "opex", "operating_income"])
            for _, r in sub.iterrows():
                expected = r["gross_profit"] - r["opex"]
                actual   = r["operating_income"]
                tol      = abs(r["revenue"]) * 0.01
                if abs(expected - actual) > tol:
                    _add(
                        "hard", "identity",
                        f"{ticker} {_fy_label(r)}: gross_profit ({r['gross_profit']:.0f}) − opex "
                        f"({r['opex']:.0f}) = {expected:.0f}; operating_income reported = "
                        f"{actual:.0f} (Δ {abs(expected-actual):.0f}, > 1% of revenue)"
                    )

        # ── HARD #9: no duplicate (fiscal_year, fiscal_quarter) per ticker ──
        if "fiscal_year" in std_q.columns:
            dupes = std_q.dropna(subset=["fiscal_year"]).groupby(["fiscal_year","fiscal_quarter"]).size()
            for (fy, fq), n in dupes.items():
                if n > 1:
                    _add("hard", "duplicate", f"{ticker}: {int(fy)}-{fq} appears {n} times (must be 1)")

        # ── SOFT #4: gross_profit < 0 (rare for tech but legal) ────────
        if "gross_profit" in std_q.columns:
            neg = std_q[std_q["gross_profit"].notna() & (std_q["gross_profit"] < 0)]
            for _, r in neg.iterrows():
                _add("soft", "sign", f"{ticker} {_fy_label(r)}: gross_profit = {r['gross_profit']:.0f} (negative — verify)")

        # ── SOFT #10: operating_income − int_exp + int_inc + other_non_op ≈ pretax_income (5%) ──
        cols = ("revenue", "operating_income", "interest_expense", "interest_income", "other_income_net", "pretax_income")
        if all(c in std_q.columns for c in cols):
            sub = std_q.dropna(subset=list(cols))
            for _, r in sub.iterrows():
                expected = r["operating_income"] - r["interest_expense"] + r["interest_income"] + r["other_income_net"]
                actual   = r["pretax_income"]
                tol      = abs(r["revenue"]) * 0.05
                if abs(expected - actual) > tol:
                    _add(
                        "soft", "identity",
                        f"{ticker} {_fy_label(r)}: op_inc − int_exp + int_inc + other = {expected:.0f}; "
                        f"pretax_income reported = {actual:.0f} (Δ {abs(expected-actual):.0f}, > 5% of revenue)"
                    )

        # ── SOFT #11: pretax_income − income_tax ≈ net_income (5%) ──────
        if all(c in std_q.columns for c in ("revenue", "pretax_income", "income_tax", "net_income")):
            sub = std_q.dropna(subset=["revenue", "pretax_income", "income_tax", "net_income"])
            for _, r in sub.iterrows():
                expected = r["pretax_income"] - r["income_tax"]
                actual   = r["net_income"]
                tol      = abs(r["revenue"]) * 0.05
                if abs(expected - actual) > tol:
                    _add(
                        "soft", "identity",
                        f"{ticker} {_fy_label(r)}: pretax ({r['pretax_income']:.0f}) − tax "
                        f"({r['income_tax']:.0f}) = {expected:.0f}; net_income reported = "
                        f"{actual:.0f} (Δ {abs(expected-actual):.0f}, > 5% of revenue)"
                    )

        # ── SOFT #12: gross_margin_pct outside [0%, 95%] ───────────────
        if "gross_margin_pct" in std_q.columns:
            sub = std_q.dropna(subset=["gross_margin_pct"])
            bad = sub[(sub["gross_margin_pct"] < 0) | (sub["gross_margin_pct"] > 95)]
            for _, r in bad.iterrows():
                _add("soft", "range", f"{ticker} {_fy_label(r)}: gross_margin_pct = {r['gross_margin_pct']:.1f}% (outside [0%, 95%])")

        # ── SOFT #13: |net_margin_pct| > 100% (impossible — can't lose more than revenue) ──
        if "net_margin_pct" in std_q.columns:
            sub = std_q.dropna(subset=["net_margin_pct"])
            bad = sub[sub["net_margin_pct"].abs() > 100]
            for _, r in bad.iterrows():
                _add("soft", "range", f"{ticker} {_fy_label(r)}: net_margin_pct = {r['net_margin_pct']:.1f}% (|value| > 100%)")

        # ── SOFT #14: effective tax rate outside [-50%, 50%] ────────────
        if all(c in std_q.columns for c in ("income_tax", "pretax_income")):
            sub = std_q.dropna(subset=["income_tax", "pretax_income"])
            sub = sub[sub["pretax_income"] != 0]
            sub["_etr"] = sub["income_tax"] / sub["pretax_income"] * 100
            bad = sub[(sub["_etr"] < -50) | (sub["_etr"] > 50)]
            for _, r in bad.iterrows():
                _add("soft", "range", f"{ticker} {_fy_label(r)}: effective tax rate = {r['_etr']:.1f}% (outside [-50%, 50%])")

        # ── SOFT #15: QoQ revenue jump > +200% or < -50% ────────────────
        if "revenue_qoq_pct" in std_q.columns:
            sub = std_q.dropna(subset=["revenue_qoq_pct"])
            bad = sub[(sub["revenue_qoq_pct"] > 200) | (sub["revenue_qoq_pct"] < -50)]
            for _, r in bad.iterrows():
                _add("soft", "cliff", f"{ticker} {_fy_label(r)}: revenue QoQ = {r['revenue_qoq_pct']:.1f}% (cliff — possible YTD-not-converted bug or M&A)")

        # Spot checks against known values
        for (sticker, end_date_str, metric, expected, tol_pct) in _SPOT_CHECKS:
            if sticker != ticker:
                continue
            if metric not in df.columns:
                spot_results.append({
                    "metric": metric, "end_date": end_date_str,
                    "status": "SKIP", "reason": "column not present",
                })
                continue
            target_date = pd.Timestamp(end_date_str)
            # Match end_date within 3 days; prefer quarterly rows (duration 80-100 days)
            close = df[((df["end_date"] - target_date).abs() <= pd.Timedelta(days=3))].copy()
            if close.empty:
                spot_results.append({
                    "metric": metric, "end_date": end_date_str,
                    "status": "SKIP", "reason": "date not found in data",
                })
                continue
            dur = (close["end_date"] - close["start_date"]).dt.days
            quarterly_rows = close[(dur >= 80) & (dur <= 100)]
            match = quarterly_rows if not quarterly_rows.empty else close
            actual = match.iloc[0][metric]
            if actual is None or (isinstance(actual, float) and np.isnan(actual)):
                spot_results.append({
                    "metric": metric, "end_date": end_date_str,
                    "status": "FAIL", "expected": expected, "actual": "NaN",
                })
                warnings_.append(
                    f"Spot check FAIL: {metric} on {end_date_str} is NaN (expected {expected})"
                )
                continue
            pct_err = abs(actual - expected) / abs(expected) * 100 if expected != 0 else abs(actual)
            ok = pct_err <= tol_pct
            spot_results.append({
                "metric": metric,
                "end_date": end_date_str,
                "status": "PASS" if ok else "FAIL",
                "expected": expected,
                "actual": round(float(actual), 4),
                "pct_error": round(pct_err, 3),
            })
            if not ok:
                warnings_.append(
                    f"Spot check FAIL: {metric} on {end_date_str} "
                    f"expected {expected}, got {actual:.4f} ({pct_err:.2f}% off)"
                )

        return {
            "warnings":       warnings_,
            "spot_checks":    spot_results,
            "quality_report": quality_report.to_dict(),
        }
