"""
topline_builder.py — builds the clean topline data layer using edgartools.

Data flow
---------
  edgartools (XBRLS.from_filings)
    → raw as-filed DataFrames (metrics as rows, period end dates as columns)
    → pivot to wide format (periods as rows, metrics as columns)
    → Q4 derivation: Q4 = Annual - Q3_YTD  (MUST run before YTD conversion)
    → YTD cumulative → standalone quarterly conversion
    → scale to millions
    → validate
    → write to topline/{statement_type}/ticker=*.parquet

Separation from backbone
------------------------
  backbone/   raw XBRL facts — never written here
  topline/    THIS builder writes here; safe to delete and rebuild
  calculated/ built on topline/, never on backbone/

Period identification strategy
-------------------------------
  edgartools exposes `fiscal_period` on each period dict:
    'Q1'/'Q2'/'Q3'/'Q4'/'FY' → standalone period; fiscal_year is RELIABLE
    None / 'N/A'              → YTD cumulative; fiscal_year may vary

  start_date invariant: Q1, Semi-Annual (H1 YTD), Nine-Months (9M YTD), and Annual
  for the SAME fiscal year all share the EXACT SAME start_date (= FY start).
  Q2/Q3 standalone and Q4 have different start_dates (= their quarter's own start).
  This lets _derive_q4 and _ytd_to_standalone group by (ticker, period_start)
  instead of any date-proximity heuristics.

Output columns
--------------
  ticker | period_end | fiscal_quarter | fiscal_year | period_start | is_ytd |
  revenue | gross_profit | ... (all metrics in millions)

  fiscal_quarter values: 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'Annual' | 'Instant'
  is_ytd: True only while value is still cumulative YTD (False after _ytd_to_standalone)

Validation
----------
  - Revenue positive for all quarters
  - Gross margin between -10% and 100%
  - Q1+Q2+Q3+Q4 ≈ Annual (within 2% tolerance)
  - Spot-checks against known NVDA values
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]
_TOPLINE_DIR = _REPO_ROOT / "backend" / "data" / "filing_data" / "topline"
_BUILD_REPORT  = _TOPLINE_DIR / "_build_report.json"
_FILING_STATE  = _TOPLINE_DIR / "_filing_state.json"   # per-ticker accession/stale tracking
_UNIVERSE_FILE = _TOPLINE_DIR / "universe.json"        # ticker universe registry
_REFRESH_LOCK  = _TOPLINE_DIR / ".refresh.lock"        # prevents concurrent refresh runs

# ---------------------------------------------------------------------------
# Concept → metric name mapping
# standard_concept from edgartools → our internal metric name
# ---------------------------------------------------------------------------

# Income statement — matched by edgartools' standard_concept column
_INCOME_MAP: dict[str, str] = {
    "Revenue":                         "revenue",
    "CostOfGoodsAndServicesSold":      "cost_of_revenue",
    "GrossProfit":                     "gross_profit",
    "TotalOperatingExpenses":          "total_opex",
    "OperatingIncomeLoss":             "operating_income",
    "ResearchAndDevelopementExpenses": "rd_expense",   # edgartools has this typo
    "ResearchAndDevelopmentExpenses":  "rd_expense",   # in case they fix it
    "SellingGeneralAndAdminExpenses":  "sga_expense",
    "InterestExpense":                 "interest_expense",
    "InterestAndDividendIncome":       "interest_income",
    "NonoperatingIncomeExpense":       "other_income_net",
    "PretaxIncomeLoss":                "pretax_income",
    "IncomeTaxes":                     "income_tax",
    "NetIncome":                       "net_income",
    # Some filers (e.g. AVGO) use ProfitLoss as the standard_concept for
    # bottom-line net income instead of NetIncome.
    "ProfitLoss":                      "net_income",
    "SharesAverage":                   "shares_basic",
    "SharesFullyDilutedAverage":       "shares_diluted",
}

# EPS matched by label (standard_concept is NaN for EPS rows)
_EPS_LABEL_MAP: dict[str, str] = {
    "diluted (in usd per share)": "eps_diluted",
    "basic (in usd per share)":   "eps_basic",
}

# Income-statement concept fallback. When standard_concept is NaN AND the
# label is too generic to match (e.g. AVGO's EPS rows are labeled just
# "Basic" / "Diluted"), we fall back to matching the raw `concept` column
# against known us-gaap concept names. Keep this map small and specific.
_INCOME_CONCEPT_FALLBACK: dict[str, str] = {
    "us-gaap_EarningsPerShareBasic":   "eps_basic",
    "us-gaap_EarningsPerShareDiluted": "eps_diluted",
    "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic":   "shares_basic",
    "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding": "shares_diluted",
}

# Sum-aggregation fallback for the income statement.
#
# Some filers (notably ORCL) don't report a single "Cost of Revenue" line or a
# "GrossProfit" subtotal. Instead they break cost of revenue into multiple
# business-segment expense rows — e.g. ORCL reports cloud+software, hardware,
# and services expenses separately, all tagged with company-specific concepts
# (orcl_*) whose standard_concept is NaN. We sum those rows into a single
# cost_of_revenue metric; gross_profit is then derived as revenue - cost_of_revenue
# in a post-processing step.
#
# Keys are our internal metric names; values are lists of raw XBRL `concept`
# aliases (the raw `concept` column, not `standard_concept`). Matching is exact.
_INCOME_SUM_MAP: dict[str, list[str]] = {
    "cost_of_revenue": [
        # Oracle — three-way split of cost of revenue. ORCL changed reporting
        # format in FY2026: pre-FY2026 uses CloudServicesAndLicenseSupport, post
        # uses CloudAndSoftware. These are mutually exclusive by period, so we
        # list both and the sum just picks up whichever is present for a given
        # quarter (no double-counting).
        "orcl_CloudServicesAndLicenseSupportExpenses",  # FY2019–FY2025
        "orcl_CloudAndSoftwareExpenses",                # FY2026+
        "orcl_HardwareExpenses",
        "orcl_ServicesExpense",
    ],
}

# Balance sheet
_BALANCE_MAP: dict[str, str] = {
    "CashAndMarketableSecurities":  "cash",
    "ShortTermInvestments":         "short_term_investments",
    "LongtermInvestments":          "long_term_investments",
    "Inventories":                  "inventories",
    "TradeReceivables":             "accounts_receivable",
    "PlantPropertyEquipmentNet":    "ppe_net",
    "Goodwill":                     "goodwill",
    "IntangibleAssets":             "intangible_assets",
    "TotalAssets":                  "total_assets",
    "TradePayables":                "accounts_payable",
    "TotalLiabilities":             "total_liabilities",
    "TotalEquity":                  "total_equity",
    "LongTermDebt":                 "long_term_debt",
}

# Cash flow statement
_CASHFLOW_MAP: dict[str, str] = {
    "NetCashFromOperatingActivities":  "operating_cf",
    "NetCashFromInvestingActivities":  "investing_cf",
    "NetCashFromFinancingActivities":  "financing_cf",
    # Capex: edgartools tags NVDA / most peers as CapitalExpenses, but some
    # filers use CapitalExpenditures. We keep both.
    "CapitalExpenses":                 "capex",
    "CapitalExpenditures":             "capex",
    "Depreciation":                    "depreciation",
    "DepreciationExpense":             "depreciation",
    "DepreciationAndAmortization":     "depreciation",
}

# Capex label fallback — some filers (e.g. NVDA FY2022-Q4..FY2024-Q2) report
# capex under a company-specific concept (nvda_PurchasesOfPropertyAndEquipment...)
# with standard_concept=NaN, so std-concept matching misses it. Match by label
# prefix instead. MUST start with "purchases" (investing activity) — the
# "principal payments on property..." line is a financing activity (debt
# repayment), NOT capex, so we explicitly require the "purchases" prefix.
_CF_LABEL_FALLBACK: dict[str, str] = {
    "capex": "purchases",  # any label starting with "purchases" AND containing
                           # "property" will match (see _process_statement)
}

# Metrics that must NOT be scaled by 1e6 (already per-share or counts)
_NO_SCALE: set[str] = {"eps_diluted", "eps_basic"}

# Some aggregation lines (especially cash flow totals like "Net cash provided
# by operating activities") share a standard_concept with multiple upstream
# sub-components in the same statement. Example: NVDA's CF statement has
# three rows tagged NetCashFromOperatingActivities -- two are working-capital
# adjustments, the last is the true total. For these metrics we keep the
# LAST matching row instead of the first.
_OVERWRITE_ON_MATCH: set[str] = {
    "operating_cf",
    "investing_cf",
    "financing_cf",
}

# Spot-checks: (ticker, period_end_str, metric, expected_value_millions, tolerance_pct)
_SPOT_CHECKS: list[tuple] = [
    ("NVDA", "2024-10-27", "revenue",          35082.0,  1.0),
    ("NVDA", "2024-10-27", "gross_profit",     26156.0,  1.0),
    ("NVDA", "2024-10-27", "operating_income", 21868.0,  2.0),
    ("NVDA", "2024-10-27", "net_income",       19309.0,  1.0),
    ("NVDA", "2024-10-27", "eps_diluted",          0.78, 2.0),
]


# ---------------------------------------------------------------------------
# ToplineBuilder
# ---------------------------------------------------------------------------

class ToplineBuilder:
    """
    Extracts clean financial statements via edgartools and stores them
    in topline/ parquets as standalone quarterly figures.

    Usage:
        builder = ToplineBuilder()
        report  = builder.build()              # all backbone tickers
        report  = builder.build(['NVDA'])      # specific tickers
        df      = builder.read('NVDA', 'income_statement')
    """

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        tickers: list[str] | None = None,
        incremental: bool = False,
    ) -> dict[str, Any]:
        """
        Build topline parquets for the given tickers.

        incremental=True:
          - Skips tickers whose existing cash_flow parquet has no NaN gaps in
            the `capex` column across standalone quarterly rows — those were
            already fully populated and don't need a rebuild.
          - For tickers that DO need a rebuild, only the cash_flow statement is
            re-processed and re-written. Income statement and balance sheet
            parquets are left untouched. Big time saver when the only change
            is a CF-side fix (e.g. the capex label fallback).
        """
        from edgar import Company, set_identity
        from edgar.xbrl import XBRLS

        set_identity("AlphaGraph Research alphagraph@research.com")

        _TOPLINE_DIR.mkdir(parents=True, exist_ok=True)
        for sub in ("income_statement", "balance_sheet", "cash_flow"):
            (_TOPLINE_DIR / sub).mkdir(exist_ok=True)

        if tickers is None:
            backbone_dir = _REPO_ROOT / "backend" / "data" / "filing_data" / "backbone"
            tickers = [p.stem.replace("ticker=", "") for p in backbone_dir.glob("ticker=*.parquet")]

        # In incremental mode, pre-filter tickers to only those needing rebuild.
        skipped: list[str] = []
        if incremental:
            needs_rebuild: list[str] = []
            for t in tickers:
                if self._cf_capex_is_complete(t):
                    skipped.append(t)
                else:
                    needs_rebuild.append(t)
            log.info("Incremental mode: %d/%d tickers already complete, %d need rebuild",
                     len(skipped), len(tickers), len(needs_rebuild))
            if skipped:
                log.info("  skipped: %s", ", ".join(skipped))
            tickers = needs_rebuild

        report: dict[str, Any] = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "incremental": incremental,
            "skipped": skipped,
            "tickers": {},
        }

        for ticker in tickers:
            log.info("Building topline for %s ...%s", ticker, " (CF only)" if incremental else "")
            ticker_report: dict[str, Any] = {}
            try:
                company = Company(ticker)
                filings = company.get_filings(form=["10-K", "10-Q"]).head(30)
                log.info("  %s: %d filings fetched", ticker, len(filings))

                xbrls = XBRLS.from_filings(filings)
                period_map = self._build_period_map(xbrls)
                # Augment with per-filing period_map entries for any period
                # XBRLS dropped during consolidation.
                period_map = self._augment_period_map_from_filings(period_map, filings)

                if not incremental:
                    # --- Income statement ---
                    is_raw  = xbrls.statements.income_statement(max_periods=40).to_dataframe()
                    is_raw  = self._gap_fill_raw_dataframe(is_raw, filings, "income_statement")
                    is_wide = self._process_statement(is_raw, period_map, _INCOME_MAP, ticker,
                                                       scale=True,
                                                       eps_label_map=_EPS_LABEL_MAP,
                                                       sum_concept_map=_INCOME_SUM_MAP,
                                                       concept_fallback_map=_INCOME_CONCEPT_FALLBACK)
                    # Q4 MUST be derived before YTD conversion: Q4 = Annual - Nine Months YTD (original)
                    # If YTD is converted to standalone first, the subtraction gives wrong Q4 values.
                    is_wide = self._derive_q4(is_wide, "income_statement")
                    is_wide = self._ytd_to_standalone(is_wide, "income_statement")
                    # Derive gross_profit from revenue - cost_of_revenue for filers
                    # who don't report a GrossProfit subtotal (e.g. ORCL). Only
                    # fill rows where gross_profit is missing AND both inputs exist.
                    is_wide = self._fill_derived_gross_profit(is_wide)
                    # Anchor-and-step-back on period labels to correct edgartools
                    # fiscal_year / fiscal_quarter mis-assignments. See
                    # .claude/skills/edgar-period-analysis/SKILL.md
                    is_wide, is_mismatches = self._reanchor_period_labels(is_wide)

                    # --- Balance sheet ---
                    bs_raw  = xbrls.statements.balance_sheet(max_periods=40).to_dataframe()
                    bs_raw  = self._gap_fill_raw_dataframe(bs_raw, filings, "balance_sheet")
                    bs_wide = self._process_statement(bs_raw, period_map, _BALANCE_MAP, ticker,
                                                       scale=True, is_instant=True)

                # --- Cash flow ---
                cf_raw  = xbrls.statements.cash_flow_statement(max_periods=40).to_dataframe()
                cf_raw  = self._gap_fill_raw_dataframe(cf_raw, filings, "cash_flow")
                cf_wide = self._process_statement(cf_raw, period_map, _CASHFLOW_MAP, ticker,
                                                   scale=True,
                                                   cf_label_fallback=_CF_LABEL_FALLBACK)
                # Same order: derive Q4 from original YTD values, then convert YTDs to standalone
                cf_wide = self._derive_q4(cf_wide, "cash_flow")
                cf_wide = self._ytd_to_standalone(cf_wide, "cash_flow")
                cf_wide, cf_mismatches = self._reanchor_period_labels(cf_wide)

                ticker_report["rows_cashflow"] = len(cf_wide)
                ticker_report["cf_reanchor_mismatches"] = cf_mismatches

                if not incremental:
                    # --- Validate (uses income statement only) ---
                    validation = self._validate(is_wide, ticker)
                    ticker_report["rows_income"]  = len(is_wide)
                    ticker_report["rows_balance"] = len(bs_wide)
                    ticker_report["validation"] = validation
                    ticker_report["is_reanchor_mismatches"] = is_mismatches

                    # --- Write all three ---
                    self._write(is_wide,  ticker, "income_statement")
                    self._write(bs_wide,  ticker, "balance_sheet")
                    self._write(cf_wide,  ticker, "cash_flow")

                    warn_count = len(validation.get("warnings", []))
                    log.info("  %s: done — IS=%d rows, %d warnings",
                             ticker, len(is_wide), warn_count)
                else:
                    # --- Write cash_flow only ---
                    self._write(cf_wide, ticker, "cash_flow")
                    log.info("  %s: done — CF=%d rows", ticker, len(cf_wide))

            except Exception as exc:
                ticker_report["error"] = str(exc)
                log.error("  %s FAILED: %s", ticker, exc, exc_info=True)

            report["tickers"][ticker] = ticker_report

        _BUILD_REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report

    # ------------------------------------------------------------------
    # Per-filing gap fill
    #
    # XBRLS.from_filings() consolidates all filings into one dataframe but
    # silently drops some period_end columns for certain filers (e.g. DELL
    # loses 2023-05-05 / 2023-08-04 / 2023-11-03, because consolidation
    # prefers comparative columns from later 10-Ks over actual 10-Q
    # filings). To get complete coverage we also extract each filing
    # individually and union any period_ends we find that weren't in the
    # consolidated output.
    # ------------------------------------------------------------------

    def _gap_fill_raw_dataframe(
        self,
        consolidated_df: "pd.DataFrame",
        filings,
        statement_name: str,
    ) -> "pd.DataFrame":
        """
        statement_name: 'income_statement' | 'balance_sheet' | 'cash_flow'

        Returns an augmented raw DataFrame with extra period-end columns
        appended when individual filings expose periods the consolidated
        XBRLS output dropped.
        """
        from edgar.xbrl import XBRL

        meta_cols = {"label", "concept", "standard_concept", "preferred_sign"}
        existing_periods = {c for c in consolidated_df.columns if c not in meta_cols and str(c).startswith("2")}

        augmented = consolidated_df.copy()

        # Walk filings newest-first so the FIRST filing to contribute a new
        # period is typically the most recent one (highest confidence).
        per_filing_values: dict[str, dict[str, float]] = {}   # period_end -> concept -> value

        for f in filings:
            try:
                xbrl = XBRL.from_filing(f)
            except Exception:
                continue
            try:
                if statement_name == "income_statement":
                    stmt = xbrl.statements.income_statement()
                elif statement_name == "cash_flow":
                    stmt = xbrl.statements.cash_flow_statement()
                elif statement_name == "balance_sheet":
                    stmt = xbrl.statements.balance_sheet()
                else:
                    continue
                raw = stmt.to_dataframe()
            except Exception:
                continue

            period_cols = [c for c in raw.columns if c not in meta_cols and str(c).startswith("2")]
            new_periods = [pc for pc in period_cols if pc not in existing_periods]
            if not new_periods:
                continue

            # For each missing period column, remember the concept -> value
            # mapping from this filing. First filing to surface the period wins.
            for pc in new_periods:
                if pc in per_filing_values:
                    continue
                col_map: dict[str, float] = {}
                for _, row in raw.iterrows():
                    concept = row.get("concept")
                    std     = row.get("standard_concept")
                    label   = row.get("label")
                    val     = row.get(pc)
                    if val is None:
                        continue
                    key = (str(std) if pd.notna(std) and std else
                           str(concept) if pd.notna(concept) and concept else
                           f"label:{label}")
                    col_map[key] = val
                per_filing_values[pc] = col_map

        if not per_filing_values:
            return augmented

        # Materialize the new period columns by aligning on concept.
        # We match consolidated rows to per-filing concepts in priority:
        #   1. standard_concept
        #   2. concept
        #   3. label
        for pc, col_map in per_filing_values.items():
            col_values = []
            for _, row in augmented.iterrows():
                std     = row.get("standard_concept")
                concept = row.get("concept")
                label   = row.get("label")
                val = None
                for candidate in [
                    (str(std) if pd.notna(std) and std else None),
                    (str(concept) if pd.notna(concept) and concept else None),
                    f"label:{label}" if label else None,
                ]:
                    if candidate and candidate in col_map:
                        val = col_map[candidate]
                        break
                col_values.append(val)
            augmented[pc] = col_values

        return augmented

    def _augment_period_map_from_filings(
        self,
        period_map: dict[str, dict],
        filings,
    ) -> dict[str, dict]:
        """
        For any period_end that ended up in the augmented raw dataframe but
        isn't in the period_map (because XBRLS.from_filings() didn't surface
        it), infer a minimal period_map entry from the per-filing metadata.

        We reprocess each filing's get_periods() call and union the entries
        into period_map, only filling gaps — existing entries are preserved.
        """
        from edgar.xbrl import XBRL

        for f in filings:
            try:
                xbrl = XBRL.from_filing(f)
                sub_map = self._build_period_map(xbrl)
            except Exception:
                continue
            for end, meta in sub_map.items():
                if end not in period_map:
                    period_map[end] = meta
        return period_map

    # ------------------------------------------------------------------
    # Period-label anchor-and-step-back
    # See .claude/skills/edgar-period-analysis/SKILL.md
    # ------------------------------------------------------------------

    @staticmethod
    def _step_back_quarter(fy: int, q_num: int, steps: int) -> tuple[int, int]:
        """Q4 -> Q3 -> Q2 -> Q1 -> prior-year Q4. steps >= 0."""
        total = fy * 4 + (q_num - 1) - steps
        return total // 4, (total % 4) + 1

    def _reanchor_period_labels(
        self, df: pd.DataFrame
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Apply the EDGAR period anchor-and-step-back rule to overwrite
        fiscal_year and fiscal_quarter for every standalone quarterly row
        and every Annual row.

        Anchors on the row with the latest period_end. Prior rows get
        labels by stepping back one fiscal quarter (for Q1-Q4 rows) or
        one fiscal year (for Annual rows) per position.

        is_ytd rows are left alone — they'll be filtered out downstream
        by the calculator and DataAgent. Rebuilding their labels would be
        meaningless because their values are still cumulative YTD, not
        standalone.

        Returns (new_df, mismatches). Mismatches list contains
        {period_end, old_label, new_label} for every row whose label
        changed — useful for build report auditing.
        """
        if df.empty:
            return df, []

        df = df.copy().reset_index(drop=True)
        mismatches: list[dict] = []

        def _label_of(row) -> str | None:
            try:
                fq = row["fiscal_quarter"]
                fy = row["fiscal_year"]
                if pd.isna(fy):
                    return None
                return f"FY{int(fy)}-{fq}"
            except Exception:
                return None

        # ---- Standalone quarters ----
        q_mask = df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"]) & (
            ~df["is_ytd"].astype(bool)
        )
        q_sub = df[q_mask].sort_values("period_end", ascending=False)
        if not q_sub.empty:
            latest_idx = q_sub.index[0]
            try:
                anchor_fy = int(df.at[latest_idx, "fiscal_year"])
                anchor_q  = int(
                    str(df.at[latest_idx, "fiscal_quarter"]).replace("Q", "")
                )
            except (TypeError, ValueError):
                anchor_fy = None
                anchor_q  = None

            if anchor_fy is not None and anchor_q is not None:
                for pos, idx in enumerate(q_sub.index):
                    old_label = _label_of(df.loc[idx])
                    new_fy, new_q = self._step_back_quarter(anchor_fy, anchor_q, pos)
                    new_label = f"FY{new_fy}-Q{new_q}"
                    df.at[idx, "fiscal_year"]    = new_fy
                    df.at[idx, "fiscal_quarter"] = f"Q{new_q}"
                    if old_label and old_label != new_label:
                        mismatches.append({
                            "period_end": str(df.at[idx, "period_end"])[:10],
                            "old":        old_label,
                            "new":        new_label,
                        })

        # ---- Annual rows ----
        a_mask = df["fiscal_quarter"] == "Annual"
        a_sub = df[a_mask].sort_values("period_end", ascending=False)
        if not a_sub.empty:
            latest_idx = a_sub.index[0]
            try:
                anchor_fy = int(df.at[latest_idx, "fiscal_year"])
            except (TypeError, ValueError):
                anchor_fy = None

            if anchor_fy is not None:
                for pos, idx in enumerate(a_sub.index):
                    old_label = _label_of(df.loc[idx])
                    new_fy = anchor_fy - pos
                    new_label = f"FY{new_fy}-Annual"
                    df.at[idx, "fiscal_year"] = new_fy
                    if old_label and old_label != new_label:
                        mismatches.append({
                            "period_end": str(df.at[idx, "period_end"])[:10],
                            "old":        old_label,
                            "new":        new_label,
                        })

        df = df.sort_values("period_end").reset_index(drop=True)
        return df, mismatches

    def _fill_derived_gross_profit(self, is_wide: pd.DataFrame) -> pd.DataFrame:
        """
        For filers that don't report a GrossProfit subtotal (e.g. ORCL), derive
        it as revenue - cost_of_revenue. Only fills rows where gross_profit is
        missing/NaN AND both revenue and cost_of_revenue are present. Leaves
        existing gross_profit values (from the GrossProfit XBRL concept) alone.
        """
        if is_wide.empty:
            return is_wide
        if "revenue" not in is_wide.columns or "cost_of_revenue" not in is_wide.columns:
            return is_wide
        if "gross_profit" not in is_wide.columns:
            is_wide["gross_profit"] = pd.NA
        mask = (
            is_wide["gross_profit"].isna()
            & is_wide["revenue"].notna()
            & is_wide["cost_of_revenue"].notna()
        )
        is_wide.loc[mask, "gross_profit"] = (
            is_wide.loc[mask, "revenue"] - is_wide.loc[mask, "cost_of_revenue"]
        ).round(4)
        return is_wide

    def _cf_capex_is_complete(self, ticker: str) -> bool:
        """
        Returns True if the ticker's existing cash_flow parquet has capex
        populated for every standalone quarterly row (Q1/Q2/Q3/Q4, is_ytd=False).
        Returns False if the parquet doesn't exist, capex column is missing,
        or any standalone quarter has NaN capex — i.e. the ticker needs a rebuild.
        """
        path = _TOPLINE_DIR / "cash_flow" / f"ticker={ticker}.parquet"
        if not path.exists():
            return False
        try:
            df = pd.read_parquet(path)
        except Exception:
            return False
        if "capex" not in df.columns:
            return False
        # Only standalone quarterly rows count — skip Annual + is_ytd rows.
        mask = (
            df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
            & (~df["is_ytd"].astype(bool))
        )
        quarterly = df[mask]
        if quarterly.empty:
            return False
        return not quarterly["capex"].isna().any()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(
        self,
        ticker: str,
        statement: str = "income_statement",
        lookback_years: float = 5.0,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Read a clean topline parquet for a single ticker.
        statement: 'income_statement' | 'balance_sheet' | 'cash_flow'

        If `columns` is provided, the parquet reader prunes columns at the
        pyarrow level — faster and lower memory for narrow queries. Required
        metadata columns (ticker, period_end, fiscal_*, etc.) are always
        included regardless of the columns list.
        """
        path = _TOPLINE_DIR / statement / f"ticker={ticker}.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"Topline not built for {ticker}/{statement}. "
                f"Run ToplineBuilder().build(['{ticker}']) first."
            )

        required = {
            "ticker", "period_end", "period_start",
            "fiscal_quarter", "fiscal_year", "is_ytd",
        }
        if columns is not None:
            read_cols = sorted(required | set(columns))
            try:
                df = pd.read_parquet(path, columns=read_cols)
            except Exception:
                # Fallback: missing column in parquet (new schema not yet rebuilt)
                df = pd.read_parquet(path)
        else:
            df = pd.read_parquet(path)

        cutoff = pd.Timestamp.now() - pd.DateOffset(years=lookback_years)
        return df[df["period_end"] >= cutoff].copy()

    def is_available(self, ticker: str) -> bool:
        return (_TOPLINE_DIR / "income_statement" / f"ticker={ticker}.parquet").exists()

    def status(self) -> dict:
        build: dict[str, Any] = {"built": False}
        if _BUILD_REPORT.exists():
            build = {"built": True, **json.loads(_BUILD_REPORT.read_text(encoding="utf-8"))}
        filing_state = self._load_filing_state()
        # Annotate stale warnings into filing state without hitting EDGAR
        for ticker, state in filing_state.items():
            stale = self._is_stale(ticker, filing_state)
            filing_state[ticker]["stale_warning"] = stale
        return {**build, "filing_state": filing_state}

    # ------------------------------------------------------------------
    # Internal: period map
    # ------------------------------------------------------------------

    def _build_period_map(self, xbrls) -> dict[str, dict]:
        """
        Rich period map keyed by period end_date (string 'YYYY-MM-DD').

        Uses edgartools' `fiscal_period` field:
          'Q1'/'Q2'/'Q3'/'Q4'/'FY' → standalone period; fiscal_year is CORRECT
          None / 'N/A'              → YTD period (Semi-Annual, Nine Months)

        For each end_date, edgartools' to_dataframe() returns the LARGEST-duration
        value.  This means:
          Q2 end_date → H1 YTD value  (is_ytd=True, canonical_fp='Q2')
          Q3 end_date → 9M YTD value  (is_ytd=True, canonical_fp='Q3')
          Q1 end_date → Q1 standalone (is_ytd=False, canonical_fp='Q1')
          Annual+Q4 shared end_date → Annual value (is_ytd=False, canonical_fp='Annual')
            canonical_fy is taken from the Q4 standalone entry when available,
            because Annual comparison columns get the FILING year (wrong), while
            the standalone Q4 entry always carries the CORRECT fiscal year.

        start_date invariant (key for matching):
          Annual, Q1, Semi-Annual, Nine Months all share the SAME start_date
          (= the fiscal year start).  Q2/Q3/Q4 standalone have different start_dates
          (= start of their individual quarter).  This lets _derive_q4 and
          _ytd_to_standalone match periods by start_date instead of heuristics.

        Returns { end_date: {
            'canonical_fp':  str|None,  # 'Q1'/'Q2'/'Q3'/'Q4'/'Annual'/'Instant'
            'canonical_fy':  int|None,  # correct fiscal year
            'is_ytd':        bool,      # True = column value is cumulative YTD
            'period_start':  str|None,  # FY start for YTD/Q1/Annual; else quarter start
            'days':          int,
        }}
        """
        # Collect ALL standalone period entries per end_date (fiscal_period != 'N/A').
        # Multiple entries per end_date happen for Q4/Annual shared dates where both a
        # Q4 standalone (~90d) and an Annual comparison column (~365d) exist.
        #
        # AMZN caveat: AMZN's filings include rolling trailing-twelve-month periods
        # labeled fp=FY at every mid-year quarter end_date (e.g. start=2024-07-01,
        # end=2025-06-30, days=364). We reject those by requiring that any "FY"
        # entry's start_date matches one of the observed Q1 standalone start_dates
        # (i.e. a genuine fiscal year start for this filer).
        all_standalone: dict[str, list[dict]] = defaultdict(list)
        ytd:            dict[str, dict]       = {}
        instant_map:    dict[str, dict]       = {}
        q1_starts:      set[str]              = set()
        # Collect all FY-labeled annual-ish periods as (fiscal_year, end_date,
        # start_date) tuples. We'll filter down to real fiscal years below.
        fy_candidates: list[tuple[Any, str, str]] = []

        raw_periods = list(xbrls.get_periods())

        # Pass 1: collect Q1 start_dates AND FY annual-period candidates
        for p in raw_periods:
            if p.get("type") != "duration":
                continue
            fp_label = p.get("fiscal_period") or ""
            sd       = p.get("start_date")
            ed       = p.get("end_date")
            days     = p.get("days", 0) or 0
            fy_val   = p.get("fiscal_year")
            if not sd:
                continue
            if fp_label == "Q1":
                q1_starts.add(sd)
            # Annual periods (fp=FY, ~365 days). We'll still filter below to
            # drop rolling-TTM "FY" entries.
            if fp_label == "FY" and 350 <= days <= 380 and ed:
                fy_candidates.append((fy_val, ed, sd))

        # Derive the set of real fiscal-year start_dates by grouping FY
        # candidates by the CALENDAR YEAR of their end_date (not by
        # edgartools' fiscal_year label, which is unreliable for DELL and
        # similar filers). For each calendar year, keep only the FY entry
        # with the latest end_date — that's the genuine fiscal year end.
        # AMZN's rolling-TTM "FY" entries lose to the real Dec-31 end, and
        # DELL's real Feb-ending annual periods all survive because each
        # falls in its own calendar year with a unique end_date.
        fy_by_calendar_year: dict[int, tuple[str, str]] = {}
        for fy_val, ed, sd in fy_candidates:
            try:
                cy = pd.Timestamp(ed).year
            except Exception:
                continue
            prev = fy_by_calendar_year.get(cy)
            if prev is None or ed > prev[0]:
                fy_by_calendar_year[cy] = (ed, sd)

        real_fy_starts: set[str] = {sd for _, sd in fy_by_calendar_year.values()}

        # Anchor set for _infer_fp: union of q1_starts (filtered to ones
        # near a real FY start) and real_fy_starts. This keeps the anchor set
        # tight so AMZN's rolling-TTM starts don't pollute the Q-label
        # inference, while including DELL's real Feb-ending fiscal year
        # starts even when edgartools mis-labels the Q1 rows.
        if real_fy_starts:
            fy_start_ts = sorted(pd.Timestamp(s) for s in real_fy_starts)

            def _near_fy_start(ts: pd.Timestamp) -> bool:
                for f in fy_start_ts:
                    if abs((ts - f).days) <= 7:
                        return True
                return False

            cleaned_q1 = {s for s in q1_starts if _near_fy_start(pd.Timestamp(s))}
            cleaned_q1.update(real_fy_starts)
            anchor_starts = cleaned_q1
        else:
            anchor_starts = set(q1_starts)

        _q1_sorted = sorted(
            (pd.Timestamp(s) for s in anchor_starts),
            reverse=True,
        )

        def _infer_fp(start_str: str, days: int) -> str | None:
            """
            Derive fiscal quarter from a period's start_date offset to the
            most recent prior Q1 start. Quarter 1-4 maps to 0/3/6/9 months.
            Returns None if we can't anchor to a Q1 start or if the period
            duration doesn't look quarterly (< 80 or > 100 days).
            """
            if not _q1_sorted or days < 80 or days > 100:
                return None
            try:
                sd = pd.Timestamp(start_str)
            except Exception:
                return None
            anchor = None
            for q1 in _q1_sorted:
                if q1 <= sd:
                    anchor = q1
                    break
            if anchor is None:
                return None
            months = (sd.year - anchor.year) * 12 + (sd.month - anchor.month)
            if months < 0 or months > 11:
                return None
            q = months // 3 + 1  # 0,1,2→Q1; 3,4,5→Q2; 6,7,8→Q3; 9,10,11→Q4
            return f"Q{q}" if q in (1, 2, 3, 4) else None

        for p in raw_periods:
            if p["type"] == "instant":
                d = p["date"]
                if d not in instant_map:
                    instant_map[d] = {
                        "canonical_fp": "Instant",
                        "canonical_fy": p.get("fiscal_year"),
                        "is_ytd":       False,
                        "period_start": None,
                        "days":         0,
                    }
                continue

            if p["type"] != "duration":
                continue
            days = p.get("days", 0)
            if days < 80:
                continue

            fp    = p.get("fiscal_period") or "N/A"
            end   = p["end_date"]
            fy    = p.get("fiscal_year")
            start = p.get("start_date")

            if fp == "FY" and real_fy_starts and start not in real_fy_starts:
                # Rolling TTM period mislabeled as FY — not a real annual period
                # for this filer. Drop it entirely.
                continue

            if fp != "N/A":
                label = "Annual" if fp == "FY" else fp
                # Narrow override: only re-infer when the label is CLEARLY
                # wrong. "Annual" on a 90-day period means edgartools assigned
                # an FY fiscal_period to a standalone quarter (AMZN case) —
                # override it. Q_ labels that are already correct for most
                # filers (NVDA, LRCX, AAPL, etc.) are left alone because our
                # inference is strictly date-based and would regress filers
                # whose Q1 starts don't align with the calendar heuristic.
                if label == "Annual" and 80 <= days <= 100:
                    inferred = _infer_fp(start, days)
                    if inferred is not None:
                        label = inferred
                all_standalone[end].append({
                    "fp":    label,
                    "fy":    fy,
                    "start": start,
                    "days":  days,
                })
            else:
                # YTD period: valid range is Semi-Annual (~180d) or Nine Months (~270d).
                # Reject anything > 320 days — edgartools occasionally stitches comparison
                # columns from prior years into a single long-duration period that would
                # corrupt the period_start grouping (seen with MU, LRCX, SNPS).
                if days > 320:
                    continue
                if end not in ytd or days > ytd[end]["days"]:
                    ytd[end] = {"start": start, "days": days}

        result: dict[str, dict] = {}

        for end, entries in all_standalone.items():
            # Sort ascending by days: shortest = Q4 standalone, longest = Annual
            entries_sorted = sorted(entries, key=lambda x: x["days"])
            # to_dataframe() returns the LARGEST-duration value → label from that entry
            value_entry = entries_sorted[-1]
            # fiscal_year from the SHORTEST entry: Q4 standalone always carries the
            # correct fiscal_year, while Annual comparison columns get the filing year
            # (e.g. NVDA FY2021 Annual inside the FY2022 10-K gets fiscal_year=2022).
            fy_entry    = entries_sorted[0]

            fp        = value_entry["fp"]
            ytd_entry = ytd.get(end)
            # is_ytd: to_dataframe() shows the LARGEST-duration value at each
            # end_date. If a YTD period (semi-annual or nine-month) exists
            # with longer duration than the standalone entry, the column
            # value is cumulative YTD — regardless of what edgartools
            # labeled the standalone as. We don't gate on fp ∈ (Q2, Q3)
            # because edgartools mis-labels some filers (DELL Q3 as "Q4",
            # some Apple quarters similarly shifted). The duration check is
            # the authoritative signal.
            is_ytd = (
                ytd_entry is not None
                and ytd_entry["days"] > value_entry["days"]
                and fp != "Annual"   # never treat a true Annual as YTD
            )
            # When we detect YTD via duration, override the fp label from
            # the YTD days: ~180 → Q2, ~270 → Q3. This corrects mis-labeled
            # rows whose edgartools fp is wrong (e.g. DELL fp=Q4 → real Q3).
            if is_ytd:
                if ytd_entry["days"] < 200:
                    fp = "Q2"
                else:
                    fp = "Q3"

            result[end] = {
                "canonical_fp":  fp,
                "canonical_fy":  fy_entry["fy"],   # from shortest entry (most reliable)
                "is_ytd":        is_ytd,
                # For YTD rows use FY start (from H1/9M start_date = same FY start).
                # For standalone Q1/Q4/Annual use the period's own start_date.
                "period_start":  ytd_entry["start"] if is_ytd else value_entry["start"],
                "days":          ytd_entry["days"]  if is_ytd else value_entry["days"],
            }

        # Add YTD-only end_dates (no standalone entry — older filings with only YTD context).
        # Distinguish by duration:
        #   80-100 days  → Q1 standalone (Q1 IS its own YTD baseline; the value
        #                  in this column is the standalone Q1 quarter, not a
        #                  cumulative YTD that needs subtraction)
        #   160-200 days → H1 YTD (Q2)
        #   250-290 days → 9M YTD (Q3)
        # AVGO's 2024-02-04 is the canonical case: edgartools provides only a
        # 97-day fp=None entry. Without this rule it was being mis-labeled as
        # "Q2 YTD" and dropped by the calculator's is_ytd filter.
        for end, y in ytd.items():
            if end not in result:
                days = y["days"]
                if 80 <= days <= 100:
                    # Standalone first quarter — same value as its own YTD.
                    # is_ytd=False so the calculator keeps it.
                    result[end] = {
                        "canonical_fp":  "Q1",
                        "canonical_fy":  None,
                        "is_ytd":        False,
                        "period_start":  y["start"],
                        "days":          days,
                    }
                else:
                    fp_guess = "Q2" if days < 200 else "Q3"
                    result[end] = {
                        "canonical_fp":  fp_guess,
                        "canonical_fy":  None,
                        "is_ytd":        True,
                        "period_start":  y["start"],
                        "days":          days,
                    }

        # Do NOT merge instant_map into result.
        # Income statement and cash flow use duration-period entries from result.
        # Balance sheet (instant) dates often coincide with duration period end dates —
        # if a balance sheet date is already in result as a duration period, that entry
        # is correct for labeling (e.g. 2024-10-27 = Q3 quarter end).
        # If a balance sheet date has NO matching duration period, period_map will return {}
        # and _process_statement will tag it as fiscal_quarter='Unknown', which is fine
        # since _derive_q4 and _ytd_to_standalone skip balance_sheet entirely.
        return result

    # ------------------------------------------------------------------
    # Internal: process one statement DataFrame
    # ------------------------------------------------------------------

    def _process_statement(
        self,
        df: pd.DataFrame,
        period_map: dict,
        concept_map: dict[str, str],
        ticker: str,
        scale: bool = True,
        is_instant: bool = False,
        eps_label_map: dict[str, str] | None = None,
        cf_label_fallback: dict[str, str] | None = None,
        sum_concept_map: dict[str, list[str]] | None = None,
        concept_fallback_map: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        """
        Pivot edgartools statement DataFrame from:
          rows=line_items, cols=[label, concept, standard_concept, *dates, preferred_sign]
        To wide format:
          rows=periods, cols=[ticker, period_end, fiscal_quarter, fiscal_year,
                               period_start, is_ytd, *metrics]

        fiscal_quarter is set directly from period_map['canonical_fp']:
          'Q1' | 'Q2' | 'Q3' | 'Annual' | 'Instant' (Q4 added later by _derive_q4)
        is_ytd=True for Q2/Q3 where the value is still cumulative YTD.
        period_start for YTD and Q1/Annual rows = FY start date (the start_date invariant).
        """
        meta_cols = {"label", "concept", "standard_concept", "preferred_sign"}
        period_cols = [c for c in df.columns if c not in meta_cols]

        # Invert sum_concept_map → {concept: metric} for O(1) lookup per row
        concept_to_sum_metric: dict[str, str] = {}
        if sum_concept_map:
            for metric_name, concepts in sum_concept_map.items():
                for c in concepts:
                    concept_to_sum_metric[c] = metric_name
        # Track which metrics in each row came from sum-aggregation so we know
        # whether to overwrite or accumulate across multiple matching rows.
        sum_accumulated: dict[int, set[str]] = {}

        rows: list[dict] = []
        for period_end in period_cols:
            meta = period_map.get(period_end, {})

            canonical_fp = meta.get("canonical_fp")
            canonical_fy = meta.get("canonical_fy")
            is_ytd       = meta.get("is_ytd", False)
            period_start = meta.get("period_start")

            row: dict[str, Any] = {
                "ticker":         ticker,
                "period_end":     pd.Timestamp(period_end),
                "fiscal_quarter": canonical_fp or "Unknown",
                "fiscal_year":    canonical_fy,
                "period_start":   pd.Timestamp(period_start) if period_start else pd.NaT,
                "is_ytd":         is_ytd,
            }

            row_idx = len(rows)
            sum_accumulated[row_idx] = set()

            for _, line in df.iterrows():
                std         = line.get("standard_concept")
                label       = str(line.get("label", "")).strip().lower()
                raw_concept = str(line.get("concept", "")).strip()
                raw_val     = line.get(period_end)

                # Resolve a row to a metric. Tiers, in priority order:
                #   1. standard_concept → concept_map (e.g. "Revenue" → revenue)
                #   2. eps_label_map (e.g. "basic (in usd per share)" → eps_basic)
                #   3. cf_label_fallback (capex: "purchases" + "property")
                #   4. concept_fallback_map (raw concept name → metric, for
                #      filers like AVGO whose EPS rows have generic labels
                #      "Basic"/"Diluted" and standard_concept=NaN)
                #   5. sum_concept_map (accumulate across multiple matching
                #      rows into one metric, e.g. ORCL's three-way COGS split)
                metric = None
                is_sum = False
                if pd.notna(std) and std in concept_map:
                    metric = concept_map[std]
                elif eps_label_map and label in eps_label_map:
                    metric = eps_label_map[label]
                elif cf_label_fallback:
                    # capex: label must START with "purchases" (investing) AND
                    # contain "property" — excludes "principal payments..." lines
                    # which are financing activities, not capex.
                    if label.startswith("purchases") and "property" in label:
                        metric = "capex"
                if metric is None and concept_fallback_map and raw_concept in concept_fallback_map:
                    metric = concept_fallback_map[raw_concept]
                if metric is None and raw_concept in concept_to_sum_metric:
                    metric = concept_to_sum_metric[raw_concept]
                    is_sum = True

                if metric is None or raw_val is None:
                    continue

                try:
                    val = float(raw_val)
                except (TypeError, ValueError):
                    continue

                if np.isnan(val):
                    continue

                if scale and metric not in _NO_SCALE:
                    val = round(val / 1_000_000, 4)
                else:
                    val = round(val, 6)

                if is_sum:
                    # Accumulate across all matching rows for this period.
                    # Sum metrics take precedence over nothing (never overwrite
                    # a non-sum match) — we only reach here if no std/label
                    # rule matched first.
                    if metric in sum_accumulated[row_idx]:
                        row[metric] = round(row.get(metric, 0.0) + val, 4)
                    else:
                        row[metric] = val
                        sum_accumulated[row_idx].add(metric)
                # Default: keep the FIRST occurrence so unexpected duplicate
                # rows don't silently overwrite good data. For a small set of
                # known aggregation metrics (CF totals) we instead keep the
                # LAST match, because the true total appears after its
                # sub-component rows in the filing.
                elif metric in _OVERWRITE_ON_MATCH:
                    row[metric] = val
                elif metric not in row:
                    row[metric] = val

            rows.append(row)

        wide = pd.DataFrame(rows)
        if wide.empty:
            return wide

        # Drop periods with no useful data
        _id_cols = {"ticker", "period_end", "fiscal_quarter", "fiscal_year",
                    "period_start", "is_ytd"}
        metric_cols = [c for c in wide.columns if c not in _id_cols]
        wide = wide.dropna(subset=metric_cols, how="all")
        wide = wide.sort_values("period_end").reset_index(drop=True)
        return wide

    # ------------------------------------------------------------------
    # Internal: YTD → standalone quarterly conversion
    # ------------------------------------------------------------------

    def _ytd_to_standalone(self, df: pd.DataFrame, statement_type: str) -> pd.DataFrame:
        """
        Convert YTD cumulative Q2/Q3 values to standalone quarterly by differencing.

        Uses the start_date invariant: Q1, Q2_YTD, Q3_YTD, and Annual for the same
        fiscal year all share the same period_start (= fiscal year start date).
        Grouping by (ticker, period_start) therefore isolates exactly the Q1-Q3_YTD-Annual
        set for one fiscal year — no fiscal_year label matching, no date windows.

        Q4 rows (added by _derive_q4, different period_start) are unaffected.

        Conversion:
          Q1 (is_ytd=False)    → kept as-is; recorded as YTD baseline
          Q2 (is_ytd=True)     → Q2_standalone = Q2_YTD - Q1
          Q3 (is_ytd=True)     → Q3_standalone = Q3_YTD - Q2_YTD_original
          Annual (is_ytd=False) → kept as-is

        Note: edgartools' fiscal_period field uses CALENDAR quarter labels for
        companies with non-December fiscal year ends (e.g. NVDA uses Feb-Jan,
        so their Q1 filing gets fp='Q2' because it ends in calendar Q2).
        This method therefore does NOT rely on fiscal_quarter=='Q1' to identify
        the baseline — it uses the first non-Annual non-YTD row by period_end.
        Fiscal quarter labels are also corrected by position after conversion.
        """
        if df.empty or statement_type == "balance_sheet":
            return df  # balance sheet is point-in-time, no YTD issue

        _id_cols = {"ticker", "period_end", "fiscal_quarter", "fiscal_year",
                    "period_start", "is_ytd"}
        # Per-share and share-count fields are NOT additive across periods —
        # subtracting a Q2-YTD EPS from a prior-period EPS produces nonsense.
        # We skip them during YTD subtraction and recompute EPS afterwards from
        # standalone net_income / shares (see _recompute_eps_after_ytd below).
        _NON_ADDITIVE: set[str] = {"eps_basic", "eps_diluted", "shares_basic", "shares_diluted"}
        metric_cols = [c for c in df.columns if c not in _id_cols and c not in _NON_ADDITIVE]

        df = df.copy()

        for ticker_val in df["ticker"].unique():
            t_mask = df["ticker"] == ticker_val

            for ps in df.loc[t_mask, "period_start"].dropna().unique():
                group_mask = t_mask & (df["period_start"] == ps)
                group = df.loc[group_mask].sort_values("period_end")

                # Only process groups that contain at least one YTD row
                if not group["is_ytd"].any():
                    continue

                prev_ytd: dict[str, float] = {}  # metric → cumulative YTD after last step
                baseline_set = False

                for idx, row in group.iterrows():
                    if not row["is_ytd"]:
                        if row["fiscal_quarter"] == "Annual":
                            continue  # keep Annual as-is, don't overwrite baseline
                        # First non-Annual non-YTD row = Q1 standalone.
                        # We do NOT check fiscal_quarter == 'Q1' here because
                        # edgartools may mislabel it as 'Q2', 'Q3', etc. for
                        # non-December fiscal year companies (NVDA, etc.).
                        if not baseline_set:
                            for col in metric_cols:
                                v = row[col]
                                if pd.notna(v):
                                    prev_ytd[col] = float(v)
                            baseline_set = True
                        continue

                    # YTD row (H1 or 9M): convert to standalone, but only mark
                    # is_ytd=False when we had a baseline to subtract from.
                    # Without a baseline, the row holds a cumulative YTD value
                    # with no standalone equivalent — leave it as is_ytd=True so
                    # the calculator filters it out. (Happens when edgartools
                    # drops the Q1/H1 comparative columns, e.g. DELL FY2024.)
                    converted_any = False
                    for col in metric_cols:
                        ytd_val = row[col]
                        if pd.notna(ytd_val) and col in prev_ytd:
                            df.at[idx, col] = round(float(ytd_val) - prev_ytd[col], 4)
                            prev_ytd[col] = float(ytd_val)
                            converted_any = True
                        elif pd.notna(ytd_val):
                            prev_ytd[col] = float(ytd_val)

                    if converted_any:
                        df.at[idx, "is_ytd"] = False

                # Relabel fiscal_quarter by position within the group (sorted by period_end).
                # Only relabel rows that are is_ytd=False (successfully converted
                # or originally standalone). YTD-only rows that couldn't be
                # converted keep their original labels and will be dropped by
                # the calculated-layer filter.
                non_annual_mask = (
                    group_mask
                    & (df["fiscal_quarter"] != "Annual")
                    & (~df["is_ytd"].astype(bool))
                )
                non_annual_idx = df.loc[non_annual_mask].sort_values("period_end").index
                position_labels = ["Q1", "Q2", "Q3"]
                for pos, idx in enumerate(non_annual_idx):
                    if pos < len(position_labels):
                        df.at[idx, "fiscal_quarter"] = position_labels[pos]

        # Retroactively adjust share counts and per-share values for stock
        # splits, so the series is continuous across split boundaries.
        # Must run BEFORE _recompute_eps: the adjusted share counts are the
        # divisor EPS is rebuilt from, and the per-row fallback needs a
        # consistent denomination to pick the right max.
        if statement_type == "income_statement":
            from .splits import apply_split_adjustments
            for ticker_val in df["ticker"].unique():
                t_mask = df["ticker"] == ticker_val
                adjusted = apply_split_adjustments(df.loc[t_mask], str(ticker_val))
                df.loc[t_mask] = adjusted

        # Recompute EPS for every row using standalone net_income / shares.
        # Post-split-adjustment, every row's shares are in a consistent
        # denomination, so the fallback logic produces sensible values even
        # for historical quarters with missing share counts.
        if statement_type == "income_statement":
            df = self._recompute_eps(df)

        return df

    def _recompute_eps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Recompute eps_basic / eps_diluted from standalone net_income and share
        counts after the YTD-to-standalone conversion.

        Fixes the bug where edgartools returns H1-YTD-weighted EPS for Q2/Q3
        filings, which was being blindly carried through (or worse, YTD-subtracted
        into negative values when net_income was positive).

        Rules:
          - Only touch rows where net_income and a positive share count exist.
          - Share-count negatives (YTD subtraction artifacts) are treated as
            invalid: we fall back to the absolute value of a nearby valid count.
          - If no valid share count is available, EPS is set to NaN.
        """
        if df.empty or "net_income" not in df.columns:
            return df
        if "shares_basic" not in df.columns and "shares_diluted" not in df.columns:
            return df

        df = df.copy()
        # Build a per-ticker fallback share count from the largest valid row
        # (usually the Annual row, which has canonical weighted-avg shares).
        for ticker_val in df["ticker"].unique():
            t_mask = df["ticker"] == ticker_val

            def _fallback(col: str) -> float | None:
                if col not in df.columns:
                    return None
                valid = df.loc[t_mask & (df[col] > 0), col]
                return float(valid.max()) if not valid.empty else None

            fb_basic   = _fallback("shares_basic")
            fb_diluted = _fallback("shares_diluted")

            for idx in df.loc[t_mask].index:
                ni = df.at[idx, "net_income"] if "net_income" in df.columns else None
                if ni is None or pd.isna(ni):
                    continue

                # shares_basic → eps_basic
                sb = df.at[idx, "shares_basic"] if "shares_basic" in df.columns else None
                if sb is None or pd.isna(sb) or sb <= 0:
                    sb = fb_basic
                if sb and sb > 0:
                    df.at[idx, "eps_basic"] = round(float(ni) / float(sb), 4)
                    df.at[idx, "shares_basic"] = float(sb)
                else:
                    df.at[idx, "eps_basic"] = float("nan")

                # shares_diluted → eps_diluted
                sd = df.at[idx, "shares_diluted"] if "shares_diluted" in df.columns else None
                if sd is None or pd.isna(sd) or sd <= 0:
                    sd = fb_diluted
                if sd and sd > 0:
                    df.at[idx, "eps_diluted"] = round(float(ni) / float(sd), 4)
                    df.at[idx, "shares_diluted"] = float(sd)
                else:
                    df.at[idx, "eps_diluted"] = float("nan")

        return df

    # ------------------------------------------------------------------
    # Internal: derive Q4
    # ------------------------------------------------------------------

    def _derive_q4(self, df: pd.DataFrame, statement_type: str) -> pd.DataFrame:
        """
        Derive Q4_standalone = Annual - Q3_YTD for each fiscal year.

        Uses the start_date invariant: Annual and Q3_YTD (Nine Months) for the same
        fiscal year share the exact same period_start (= fiscal year start date).
        Grouping by (ticker, period_start) finds the correct pair precisely, with no
        date-proximity heuristics and no fiscal_year label matching.

        Must be called BEFORE _ytd_to_standalone so Q3 is still the original 9M YTD.

        The derived Q4 row gets:
          period_end   = Annual.period_end   (last day of fiscal year)
          period_start = Q3_YTD.period_end + 1 day  (start of Q4 quarter)
          fiscal_year  = Annual.fiscal_year  (now correct via canonical_fy fix in _build_period_map)
          is_ytd       = False
        """
        if df.empty or statement_type == "balance_sheet":
            return df

        _id_cols = {"ticker", "period_end", "fiscal_quarter", "fiscal_year",
                    "period_start", "is_ytd"}
        # Per-share and share-count fields are NOT additive — skip them here and
        # let _recompute_eps() fix them from standalone net_income after conversion.
        _NON_ADDITIVE: set[str] = {"eps_basic", "eps_diluted", "shares_basic", "shares_diluted"}
        metric_cols = [c for c in df.columns if c not in _id_cols and c not in _NON_ADDITIVE]

        new_rows: list[dict] = []

        for ticker_val in df["ticker"].unique():
            t_mask = df["ticker"] == ticker_val

            for ps in df.loc[t_mask, "period_start"].dropna().unique():
                group_mask = t_mask & (df["period_start"] == ps)
                group = df.loc[group_mask]

                # Annual shares period_start (= FY start) with all YTD rows for that year.
                annual_rows = group[group["fiscal_quarter"] == "Annual"]
                # Use ALL YTD rows regardless of fiscal_quarter label:
                # edgartools may assign calendar-quarter labels (e.g. 'Q3') to both
                # H1 YTD and 9M YTD for non-December fiscal year companies (NVDA, etc.).
                # We want the LATEST YTD row (largest period_end = 9M, not H1).
                ytd_rows = group[group["is_ytd"] == True]

                if annual_rows.empty or ytd_rows.empty:
                    continue

                annual = annual_rows.iloc[0]
                # Latest YTD row by period_end = the Nine-Months (9M) YTD, correct for Q4 derivation
                q3     = ytd_rows.sort_values("period_end").iloc[-1]

                q4_row: dict[str, Any] = {
                    "ticker":         ticker_val,
                    "period_end":     annual["period_end"],
                    "fiscal_quarter": "Q4",
                    "fiscal_year":    annual["fiscal_year"],  # correct via canonical_fy
                    "period_start":   q3["period_end"] + pd.Timedelta(days=1),
                    "is_ytd":         False,
                }
                for col in metric_cols:
                    a = annual.get(col)
                    q = q3.get(col)
                    if pd.notna(a) and pd.notna(q):
                        q4_row[col] = round(float(a) - float(q), 4)
                    else:
                        q4_row[col] = np.nan

                new_rows.append(q4_row)

        if new_rows:
            q4_df = pd.DataFrame(new_rows)
            df = pd.concat([df, q4_df], ignore_index=True)

        df = df.sort_values(["ticker", "fiscal_year", "period_end"]).reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Internal: validate
    # ------------------------------------------------------------------

    def _validate(self, df: pd.DataFrame, ticker: str) -> dict[str, Any]:
        warnings_: list[str] = []
        spot_results: list[dict] = []

        if df.empty:
            warnings_.append("Income statement DataFrame is empty.")
            return {"warnings": warnings_, "spot_checks": spot_results}

        # Revenue must be positive for standalone quarters
        q_rows = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])]
        if "revenue" in q_rows.columns:
            neg_rev = q_rows[q_rows["revenue"].notna() & (q_rows["revenue"] < 0)]
            if not neg_rev.empty:
                warnings_.append(
                    f"revenue: {len(neg_rev)} quarterly rows with negative revenue — "
                    f"likely YTD conversion error"
                )

        # Gross margin sanity
        if "revenue" in df.columns and "gross_profit" in df.columns:
            gm = df["gross_profit"] / df["revenue"] * 100
            extreme = gm[(gm.abs() > 100) & gm.notna()]
            if not extreme.empty:
                warnings_.append(
                    f"gross_margin: {len(extreme)} rows outside -100%/+100% "
                    f"(range {gm.min():.1f}% to {gm.max():.1f}%)"
                )

        # FY reconciliation: Q1+Q2+Q3+Q4 ≈ Annual within 2%.
        # Strategy: use period_start (= FY start date) as the grouping key.
        # Annual, Q1, Q2, Q3 all share period_start = FY start.
        # Q4 has a different period_start (Q3_end + 1 day), so find it by period_end match.
        # This eliminates all heuristics (fiscal_year label matching, 365-day windows)
        # and handles fiscal year transitions (e.g. CDNS) naturally.
        if "revenue" in df.columns:
            annual_df = df[df["fiscal_quarter"] == "Annual"]
            q4_df     = df[df["fiscal_quarter"] == "Q4"]

            for _, ann in annual_df.iterrows():
                ann_end     = ann["period_end"]
                ann_revenue = ann.get("revenue")
                ann_ps      = ann.get("period_start")
                if pd.isna(ann_revenue) or ann_revenue == 0:
                    continue

                # Q4 was derived from this Annual row; they share the same period_end
                q4_match = q4_df[q4_df["period_end"] == ann_end]
                if q4_match.empty:
                    continue
                q4_fy = q4_match.iloc[0]["fiscal_year"]

                # Q1+Q2+Q3: same period_start as Annual (= FY start).
                # This is the key elegance: no year labels, no date windows needed.
                if pd.notna(ann_ps):
                    base_mask = (
                        (df["period_start"] == ann_ps) &
                        (df["fiscal_quarter"].isin(["Q1", "Q2", "Q3"]))
                    )
                else:
                    # Fallback if period_start missing: fiscal_year + date window
                    base_mask = (
                        (df["fiscal_year"] == q4_fy) &
                        (df["fiscal_quarter"].isin(["Q1", "Q2", "Q3"])) &
                        (df["period_end"] < ann_end) &
                        (df["period_end"] >= ann_end - pd.Timedelta(days=400))
                    )

                other_qs  = df[base_mask]["revenue"].sum()
                quarterly = other_qs + q4_match["revenue"].sum()
                pct_diff  = abs(quarterly - ann_revenue) / abs(ann_revenue) * 100
                if pct_diff > 2.0:
                    warnings_.append(
                        f"FY{q4_fy} revenue: Q1+Q2+Q3+Q4 ({quarterly:.0f}M) "
                        f"vs Annual ({ann_revenue:.0f}M) — {pct_diff:.1f}% discrepancy"
                    )

        # Spot checks
        for (sticker, end_str, metric, expected, tol_pct) in _SPOT_CHECKS:
            if sticker != ticker:
                continue
            if metric not in df.columns:
                spot_results.append({"metric": metric, "end_date": end_str,
                                     "status": "SKIP", "reason": "column missing"})
                continue
            target = pd.Timestamp(end_str)
            matches = df[(df["period_end"] - target).abs() <= pd.Timedelta(days=3)]
            # Prefer quarterly standalone rows over Annual
            standalone = matches[matches["fiscal_quarter"].isin(["Q1","Q2","Q3","Q4"])]
            row = standalone.iloc[0] if not standalone.empty else (
                matches.iloc[0] if not matches.empty else None)

            if row is None:
                spot_results.append({"metric": metric, "end_date": end_str,
                                     "status": "SKIP", "reason": "date not found"})
                continue

            actual = row[metric]
            if actual is None or (isinstance(actual, float) and np.isnan(actual)):
                spot_results.append({"metric": metric, "end_date": end_str,
                                     "status": "FAIL", "expected": expected, "actual": "NaN"})
                warnings_.append(f"Spot check FAIL: {metric}@{end_str} is NaN (expected {expected})")
                continue

            pct_err = abs(float(actual) - expected) / abs(expected) * 100
            ok = pct_err <= tol_pct
            spot_results.append({
                "metric": metric, "end_date": end_str,
                "status": "PASS" if ok else "FAIL",
                "expected": expected, "actual": round(float(actual), 4),
                "pct_error": round(pct_err, 3),
            })
            if not ok:
                warnings_.append(
                    f"Spot check FAIL: {metric}@{end_str} expected {expected}, "
                    f"got {actual:.2f} ({pct_err:.2f}% off)"
                )

        return {"warnings": warnings_, "spot_checks": spot_results}

    # ------------------------------------------------------------------
    # Internal: write
    # ------------------------------------------------------------------

    def _write(self, df: pd.DataFrame, ticker: str, statement: str) -> None:
        out = _TOPLINE_DIR / statement / f"ticker={ticker}.parquet"
        df.to_parquet(out, index=False, engine="pyarrow")
        log.debug("  wrote %s rows to %s", len(df), out)

    # ------------------------------------------------------------------
    # Universe registry
    # ------------------------------------------------------------------

    def _load_universe(self) -> list[str]:
        """
        Return the universe ticker list.
        Falls back to backbone/ parquet filenames if universe.json doesn't exist yet.
        """
        if _UNIVERSE_FILE.exists():
            return json.loads(_UNIVERSE_FILE.read_text(encoding="utf-8")).get("tickers", [])
        backbone_dir = _REPO_ROOT / "backend" / "data" / "filing_data" / "backbone"
        return [p.stem.replace("ticker=", "") for p in backbone_dir.glob("ticker=*.parquet")]

    def _save_universe(self, tickers: list[str]) -> None:
        _TOPLINE_DIR.mkdir(parents=True, exist_ok=True)
        _UNIVERSE_FILE.write_text(
            json.dumps({"tickers": tickers,
                        "updated_at": datetime.now(timezone.utc).isoformat()},
                       indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Filing state persistence
    # ------------------------------------------------------------------

    def _load_filing_state(self) -> dict[str, Any]:
        if _FILING_STATE.exists():
            return json.loads(_FILING_STATE.read_text(encoding="utf-8"))
        return {}

    def _save_filing_state(self, state: dict[str, Any]) -> None:
        _TOPLINE_DIR.mkdir(parents=True, exist_ok=True)
        _FILING_STATE.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # EDGAR filing metadata helpers
    # ------------------------------------------------------------------

    def _get_filing_info(self, company) -> dict[str, dict]:
        """
        Fetch the latest accession number for 10-K, 10-Q, 10-K/A and 10-Q/A.

        Returns e.g.:
          {
            "10-K":   {"accession": "0000320193-25-000001", "filed": "2025-11-01", "period": "2025-09-27"},
            "10-Q":   {"accession": "0000320193-25-000042", ...},
            "10-K/A": None,   # key absent when no filing of that form exists
            "10-Q/A": None,
          }
        """
        info: dict[str, dict] = {}
        for form in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            try:
                filing = company.get_filings(form=form).latest()
                if filing is not None:
                    info[form] = {
                        "accession": str(filing.accession_no),
                        "filed":     str(filing.filing_date),
                        "period":    str(filing.period_of_report),
                    }
            except Exception as exc:
                log.debug("  Could not fetch %s for %s: %s", form, company.ticker, exc)
        return info

    def _extract_last_period_end(self, ticker: str) -> str | None:
        """Read the most recent standalone quarter period_end from the built parquet."""
        try:
            path = _TOPLINE_DIR / "income_statement" / f"ticker={ticker}.parquet"
            if not path.exists():
                return None
            df = pd.read_parquet(path, columns=["period_end", "fiscal_quarter"])
            qtrs = df[df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])]
            if qtrs.empty:
                return None
            return str(qtrs["period_end"].max().date())
        except Exception:
            return None

    def _is_stale(self, ticker: str, filing_state: dict[str, Any]) -> str | None:
        """
        Return a warning string if last known period_end is > 50 days ago, else None.
        Uses filing_state cache first; falls back to reading the parquet.
        """
        last_period = (filing_state.get(ticker) or {}).get("last_period_end")
        if not last_period:
            last_period = self._extract_last_period_end(ticker)
        if not last_period:
            return None
        try:
            last_dt = date.fromisoformat(last_period)
        except ValueError:
            return None
        days_ago = (date.today() - last_dt).days
        if days_ago > 50:
            return (
                f"Last period end {last_period} is {days_ago} days ago (>50 days stale)"
            )
        return None

    # ------------------------------------------------------------------
    # Public: check for updates (read-only, no rebuild)
    # ------------------------------------------------------------------

    def check_updates(self, tickers: list[str] | None = None) -> dict[str, Any]:
        """
        Poll EDGAR for new filings and report what has changed — without rebuilding.

        Per-ticker result:
          has_new_filing     bool   — any of 10-K/10-Q/10-K/A/10-Q/A has a new accession
          is_amendment_update bool  — change is driven solely by 10-K/A or 10-Q/A
          stale_warning      str|None
          current_filings    dict   — raw accession/filed/period from EDGAR
          last_built         str|None
          last_period_end    str|None
        """
        from edgar import Company, set_identity
        set_identity("AlphaGraph Research alphagraph@research.com")

        if tickers is None:
            tickers = self._load_universe()

        filing_state = self._load_filing_state()
        result: dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "tickers": {},
        }

        for ticker in tickers:
            try:
                company = Company(ticker)
                current = self._get_filing_info(company)
                known   = filing_state.get(ticker, {})

                new_10k  = current.get("10-K",   {}).get("accession") != known.get("10k_accession")
                new_10q  = current.get("10-Q",   {}).get("accession") != known.get("10q_accession")
                new_10ka = current.get("10-K/A", {}).get("accession") != known.get("10ka_accession")
                new_10qa = current.get("10-Q/A", {}).get("accession") != known.get("10qa_accession")

                has_new      = new_10k or new_10q or new_10ka or new_10qa
                is_amendment = (new_10ka or new_10qa) and not (new_10k or new_10q)

                result["tickers"][ticker] = {
                    "has_new_filing":     has_new,
                    "is_amendment_update": is_amendment,
                    "stale_warning":      self._is_stale(ticker, filing_state),
                    "current_filings":    current,
                    "last_built":         known.get("last_built_at"),
                    "last_period_end":    known.get("last_period_end"),
                }
            except Exception as exc:
                log.error("  check_updates %s failed: %s", ticker, exc)
                result["tickers"][ticker] = {"error": str(exc)}

        return result

    # ------------------------------------------------------------------
    # Public: incremental refresh
    # ------------------------------------------------------------------

    def refresh(
        self,
        tickers: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Incremental refresh: detect new EDGAR filings, rebuild only changed tickers.

        Logic per ticker:
          1. Fetch latest 10-K / 10-Q / 10-K/A / 10-Q/A accession numbers from EDGAR.
          2. Compare against _filing_state.json.
          3. Rebuild if any accession changed OR force=True.
          4. Mark is_amendment_update=True when ONLY an amendment (10-K/A / 10-Q/A)
             changed (no new base 10-K or 10-Q).
          5. Update _filing_state.json after each rebuild.

        Concurrency: a .refresh.lock file prevents overlapping runs.
        """
        from edgar import Company, set_identity
        set_identity("AlphaGraph Research alphagraph@research.com")

        _TOPLINE_DIR.mkdir(parents=True, exist_ok=True)

        if _REFRESH_LOCK.exists():
            return {"error": "Another refresh is already running (.refresh.lock exists). "
                             "Delete the lock file manually if the previous run crashed."}

        _REFRESH_LOCK.touch()
        try:
            if tickers is None:
                tickers = self._load_universe()

            filing_state = self._load_filing_state()
            report: dict[str, Any] = {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "tickers": {},
            }

            for ticker in tickers:
                ticker_result: dict[str, Any] = {}
                try:
                    company = Company(ticker)
                    current = self._get_filing_info(company)
                    known   = filing_state.get(ticker, {})

                    new_10k  = current.get("10-K",   {}).get("accession") != known.get("10k_accession")
                    new_10q  = current.get("10-Q",   {}).get("accession") != known.get("10q_accession")
                    new_10ka = current.get("10-K/A", {}).get("accession") != known.get("10ka_accession")
                    new_10qa = current.get("10-Q/A", {}).get("accession") != known.get("10qa_accession")

                    has_new      = new_10k or new_10q or new_10ka or new_10qa
                    is_amendment = (new_10ka or new_10qa) and not (new_10k or new_10q)
                    stale        = self._is_stale(ticker, filing_state)

                    if has_new or force:
                        log.info("  %s: new filing detected (10K=%s 10Q=%s 10K/A=%s 10Q/A=%s) — rebuilding",
                                 ticker, new_10k, new_10q, new_10ka, new_10qa)
                        build_report  = self.build([ticker])
                        ticker_build  = build_report["tickers"].get(ticker, {})
                        last_period   = self._extract_last_period_end(ticker)

                        # Persist updated filing state for this ticker
                        filing_state[ticker] = {
                            "10k_accession":       current.get("10-K",   {}).get("accession"),
                            "10q_accession":       current.get("10-Q",   {}).get("accession"),
                            "10ka_accession":      current.get("10-K/A", {}).get("accession"),
                            "10qa_accession":      current.get("10-Q/A", {}).get("accession"),
                            "last_built_at":       datetime.now(timezone.utc).isoformat(),
                            "last_period_end":     last_period,
                            "is_amendment_update": is_amendment,
                        }

                        ticker_result = {
                            "action":              "rebuilt",
                            "is_amendment_update": is_amendment,
                            "stale_warning":       stale,
                            "rows_income":         ticker_build.get("rows_income"),
                            "rows_balance":        ticker_build.get("rows_balance"),
                            "rows_cashflow":       ticker_build.get("rows_cashflow"),
                            "warnings":            ticker_build.get("validation", {}).get("warnings", []),
                        }
                        if "error" in ticker_build:
                            ticker_result["error"] = ticker_build["error"]
                    else:
                        ticker_result = {
                            "action":        "skipped",
                            "reason":        "no new filing detected",
                            "stale_warning": stale,
                        }

                except Exception as exc:
                    ticker_result = {"action": "error", "error": str(exc)}
                    log.error("  %s refresh FAILED: %s", ticker, exc, exc_info=True)

                report["tickers"][ticker] = ticker_result

            self._save_filing_state(filing_state)
            return report

        finally:
            _REFRESH_LOCK.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Public: add ticker to universe + full first-time build
    # ------------------------------------------------------------------

    def add_ticker(self, ticker: str) -> dict[str, Any]:
        """
        Add a new ticker to the universe and run a full first-time build.

        Steps:
          1. Register ticker in universe.json.
          2. Build topline (full EDGAR history).
          3. Seed _filing_state.json with current accession numbers.
          4. Build the calculated layer for this ticker.

        Returns a combined report covering topline + calculated build results.
        """
        from edgar import Company, set_identity
        set_identity("AlphaGraph Research alphagraph@research.com")

        ticker = ticker.upper().strip()

        # Register in universe
        universe = self._load_universe()
        if ticker not in universe:
            universe.append(ticker)
            self._save_universe(universe)
            log.info("  Added %s to universe (%d tickers total)", ticker, len(universe))
        else:
            log.info("  %s already in universe — rebuilding anyway", ticker)

        # Full topline build
        build_report = self.build([ticker])
        ticker_build = build_report["tickers"].get(ticker, {})

        if "error" in ticker_build:
            return {"ticker": ticker, "status": "error", "error": ticker_build["error"]}

        # Seed filing state
        try:
            company       = Company(ticker)
            current       = self._get_filing_info(company)
            last_period   = self._extract_last_period_end(ticker)
            filing_state  = self._load_filing_state()
            filing_state[ticker] = {
                "10k_accession":       current.get("10-K",   {}).get("accession"),
                "10q_accession":       current.get("10-Q",   {}).get("accession"),
                "10ka_accession":      current.get("10-K/A", {}).get("accession"),
                "10qa_accession":      current.get("10-Q/A", {}).get("accession"),
                "last_built_at":       datetime.now(timezone.utc).isoformat(),
                "last_period_end":     last_period,
                "is_amendment_update": False,
            }
            self._save_filing_state(filing_state)
        except Exception as exc:
            log.warning("  Could not seed filing state for %s: %s", ticker, exc)

        # Calculated layer build
        calc_result: dict[str, Any] = {}
        try:
            from backend.app.services.data_agent.calculator import CalculatedLayerBuilder
            calc_result = CalculatedLayerBuilder().build(tickers=[ticker])
        except Exception as exc:
            log.warning("  Calculated layer build failed for %s: %s", ticker, exc)
            calc_result = {"error": str(exc)}

        return {
            "ticker":              ticker,
            "status":              "built",
            "rows_income":         ticker_build.get("rows_income"),
            "rows_balance":        ticker_build.get("rows_balance"),
            "rows_cashflow":       ticker_build.get("rows_cashflow"),
            "validation_warnings": ticker_build.get("validation", {}).get("warnings", []),
            "calculated_layer":    calc_result,
        }
