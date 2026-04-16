"""
concept_map.py — maps human-readable metric names to XBRL concept aliases.

Different filers use different concept names for the same economic metric.
Each entry lists concepts in priority order; the DataAgent uses the first
non-null value found per ticker/period.

Computed metrics (ratios, YoY growth) are derived from base metrics in
data_agent.py after the raw fetch — they never appear in SQL directly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Base metrics: fetched directly from parquet via SQL
# ---------------------------------------------------------------------------

# Each value is a list of XBRL concept aliases, highest-priority first.
BASE_METRIC_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        # Priority: largest-value concept wins via MAX aggregation in SQL.
        # RevenueFromContract is preferred for companies that file both (e.g. AAPL)
        # because us-gaap:Revenues can also be used for small hedging adjustments.
        # NVDA only files us-gaap:Revenues, so it falls through correctly.
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
        "us-gaap:SalesRevenueGoodsNet",
    ],
    "cost_of_revenue": [
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:CostOfGoodsSold",
    ],
    "gross_profit": [
        "us-gaap:GrossProfit",
    ],
    "operating_income": [
        "us-gaap:OperatingIncomeLoss",
    ],
    "net_income": [
        "us-gaap:NetIncomeLoss",
        "us-gaap:ProfitLoss",
    ],
    "eps_diluted": [
        "us-gaap:EarningsPerShareDiluted",
    ],
    "eps_basic": [
        "us-gaap:EarningsPerShareBasic",
    ],
    "rd_expense": [
        "us-gaap:ResearchAndDevelopmentExpense",
    ],
    "sga_expense": [
        "us-gaap:SellingGeneralAndAdministrativeExpense",
        "us-gaap:GeneralAndAdministrativeExpense",
    ],
    "operating_cf": [
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    ],
    "investing_cf": [
        "us-gaap:NetCashProvidedByUsedInInvestingActivities",
    ],
    "financing_cf": [
        "us-gaap:NetCashProvidedByUsedInFinancingActivities",
    ],
    "capex": [
        "us-gaap:PaymentsToAcquireProductiveAssets",
        "us-gaap:PaymentsForCapitalImprovements",
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
    ],
    "depreciation": [
        "us-gaap:DepreciationDepletionAndAmortization",
        "us-gaap:DepreciationAndAmortization",
        "us-gaap:Depreciation",
    ],
    "shares_diluted": [
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "shares_basic": [
        "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    # Below-the-line P&L items. The topline parquet already populates these via
    # _INCOME_MAP in topline_builder.py — we declare them here so DataAgent's
    # "unknown metric" filter doesn't drop them when consumers request them.
    "interest_expense": [
        "us-gaap:InterestExpense",
    ],
    "interest_income": [
        "us-gaap:InterestIncome",
        "us-gaap:InvestmentIncomeInterest",
    ],
    "other_income_net": [
        "us-gaap:NonoperatingIncomeExpense",
        "us-gaap:OtherNonoperatingIncomeExpense",
    ],
    "pretax_income": [
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "income_tax": [
        "us-gaap:IncomeTaxExpenseBenefit",
    ],
    "total_opex": [
        "us-gaap:OperatingExpenses",
        "us-gaap:CostsAndExpenses",
    ],
}

# ---------------------------------------------------------------------------
# Computed metrics: derived in Python after fetching base metrics
# order matters — dependencies listed first
# ---------------------------------------------------------------------------

COMPUTED_METRICS: dict[str, dict] = {
    "gross_margin_pct": {
        "requires": ["gross_profit", "revenue"],
        "formula": lambda d: round(d["gross_profit"] / d["revenue"] * 100, 2),
        "label": "Gross Margin %",
        "unit": "%",
    },
    "operating_margin_pct": {
        "requires": ["operating_income", "revenue"],
        "formula": lambda d: round(d["operating_income"] / d["revenue"] * 100, 2),
        "label": "Operating Margin %",
        "unit": "%",
    },
    "net_margin_pct": {
        "requires": ["net_income", "revenue"],
        "formula": lambda d: round(d["net_income"] / d["revenue"] * 100, 2),
        "label": "Net Margin %",
        "unit": "%",
    },
    "ebitda": {
        "requires": ["operating_income", "depreciation"],
        "formula": lambda d: round(d["operating_income"] + abs(d["depreciation"]), 2),
        "label": "EBITDA",
        "unit": "M",
    },
    "free_cash_flow": {
        "requires": ["operating_cf", "capex"],
        # capex stored as negative in SEC filings (cash outflow)
        "formula": lambda d: round(d["operating_cf"] + d["capex"], 2),
        "label": "Free Cash Flow",
        "unit": "M",
    },
    "rd_pct_revenue": {
        "requires": ["rd_expense", "revenue"],
        "formula": lambda d: round(d["rd_expense"] / d["revenue"] * 100, 2),
        "label": "R&D % of Revenue",
        "unit": "%",
    },
    "opex": {
        # Total operating expenses = everything below gross profit that reduces it to operating income.
        # Standard SEC definition: Gross Profit - Operating Income = SG&A + R&D + other opex.
        "requires": ["gross_profit", "operating_income"],
        "formula": lambda d: round(d["gross_profit"] - d["operating_income"], 2),
        "label": "Operating Expenses",
        "unit": "M",
    },
}

# ---------------------------------------------------------------------------
# Display hints used by DisplayAgent
# ---------------------------------------------------------------------------

METRIC_META: dict[str, dict] = {
    "revenue":              {"label": "Net Revenue",          "unit": "M", "good_high": True},
    "gross_profit":         {"label": "Gross Profit",         "unit": "M", "good_high": True},
    "operating_income":     {"label": "Operating Income",     "unit": "M", "good_high": True},
    "net_income":           {"label": "Net Income",           "unit": "M", "good_high": True},
    "eps_diluted":          {"label": "EPS (Diluted)",        "unit": "$", "good_high": True},
    "rd_expense":           {"label": "R&D Expense",          "unit": "M", "good_high": None},
    "sga_expense":          {"label": "SG&A Expense",         "unit": "M", "good_high": None},
    "operating_cf":         {"label": "Operating Cash Flow",  "unit": "M", "good_high": True},
    "investing_cf":         {"label": "Investing Cash Flow",  "unit": "M", "good_high": None},
    "financing_cf":         {"label": "Financing Cash Flow",  "unit": "M", "good_high": None},
    "capex":                {"label": "Capex",                "unit": "M", "good_high": None},
    "free_cash_flow":       {"label": "Free Cash Flow",       "unit": "M", "good_high": True},
    "ebitda":               {"label": "EBITDA",               "unit": "M", "good_high": True},
    "depreciation":         {"label": "D&A",                  "unit": "M", "good_high": None},
    "gross_margin_pct":     {"label": "Gross Margin %",       "unit": "%", "good_high": True},
    "operating_margin_pct": {"label": "Operating Margin %",   "unit": "%", "good_high": True},
    "net_margin_pct":       {"label": "Net Margin %",         "unit": "%", "good_high": True},
    "rd_pct_revenue":       {"label": "R&D % Revenue",        "unit": "%", "good_high": None},
    "cost_of_revenue":      {"label": "Cost of Sales",        "unit": "M", "good_high": False},
    "opex":                 {"label": "OpEx",                 "unit": "M", "good_high": False},
    "eps_basic":            {"label": "EPS (Basic)",          "unit": "$", "good_high": True},
}

# ---------------------------------------------------------------------------
# Dimension columns that must ALL be NULL for a consolidated (non-segmented) fact
# ---------------------------------------------------------------------------

CONSOLIDATION_DIM_COLS: list[str] = [
    "dim_us-gaap_StatementBusinessSegmentsAxis",
    "dim_srt_StatementGeographicalAxis",
    "dim_srt_ProductOrServiceAxis",
    "dim_srt_ConsolidationItemsAxis",
]

# ---------------------------------------------------------------------------
# Period duration windows (days)
# ---------------------------------------------------------------------------

PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "quarterly": (80, 100),
    "annual":    (340, 380),
    "ttm":       (340, 380),  # handled specially in data_agent.py
}

# ---------------------------------------------------------------------------
# Metrics that are NOT denominated in USD (do not divide by 1e6)
# ---------------------------------------------------------------------------

# EPS is dollars-per-share; shares are a count.
RAW_SCALE_METRICS: set[str] = {"eps_diluted", "eps_basic", "shares_diluted"}

# ---------------------------------------------------------------------------
# Temporal metrics — pre-computed in the calculated layer (YoY %, QoQ %)
#
# These are NOT available from raw parquet. They live in:
#   backend/data/filing_data/calculated/ticker=*.parquet
#
# "base_metric" is the column in the calculated layer this is derived from.
# ---------------------------------------------------------------------------

# Metrics for which we compute YoY and QoQ growth.
# Add a metric here if it should get _yoy_pct and _qoq_pct columns.
GROWTH_BASE_METRICS: list[str] = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cf",
    "free_cash_flow",
    "rd_expense",
]
# Note: operating_income is already here, so operating_income_yoy_pct / _qoq_pct
# are auto-generated in TEMPORAL_METRICS below and emitted by calculator.py.

# Auto-generate YoY metric descriptors from GROWTH_BASE_METRICS
YOY_METRICS: dict[str, dict] = {
    f"{m}_yoy_pct": {
        "base_metric": m,
        "label": f"{METRIC_META.get(m, {}).get('label', m)} YoY %",
        "unit": "%",
        "good_high": METRIC_META.get(m, {}).get("good_high"),
    }
    for m in GROWTH_BASE_METRICS
}

# Auto-generate QoQ metric descriptors from GROWTH_BASE_METRICS
QOQ_METRICS: dict[str, dict] = {
    f"{m}_qoq_pct": {
        "base_metric": m,
        "label": f"{METRIC_META.get(m, {}).get('label', m)} QoQ %",
        "unit": "%",
        "good_high": METRIC_META.get(m, {}).get("good_high"),
    }
    for m in GROWTH_BASE_METRICS
}

# ---------------------------------------------------------------------------
# Margin deltas — YoY percentage-point difference on margin metrics.
#
# Unlike *_yoy_pct (which is a growth RATE), these are absolute percentage-point
# differences: current margin % minus same-metric 4 quarters ago, in pp.
# Only meaningful for metrics already expressed in %.
# ---------------------------------------------------------------------------
MARGIN_DELTA_BASE_METRICS: list[str] = [
    "gross_margin_pct",
    "operating_margin_pct",
    "net_margin_pct",
]

MARGIN_DELTA_METRICS: dict[str, dict] = {
    f"{m}_diff_yoy": {
        "base_metric": m,
        "label": f"{METRIC_META.get(m, {}).get('label', m)} Δ YoY (pp)",
        "unit": "pp",
        "good_high": METRIC_META.get(m, {}).get("good_high"),
    }
    for m in MARGIN_DELTA_BASE_METRICS
}

# All temporal (growth) metric names — only available from calculated layer
TEMPORAL_METRICS: dict[str, dict] = {
    **YOY_METRICS,
    **QOQ_METRICS,
    **MARGIN_DELTA_METRICS,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All metrics the DataAgent can serve (base + computed + temporal)
ALL_METRICS = (
    set(BASE_METRIC_CONCEPTS.keys())
    | set(COMPUTED_METRICS.keys())
    | set(TEMPORAL_METRICS.keys())
)


def resolve_base_dependencies(metrics: list[str]) -> list[str]:
    """
    Given a list of requested metrics (may include computed/temporal ones),
    returns the full list of BASE metrics that must be fetched from raw parquet.
    Temporal metrics resolve via their underlying base_metric → computed dep chain.
    """
    needed: set[str] = set()
    for m in metrics:
        if m in BASE_METRIC_CONCEPTS:
            needed.add(m)
        elif m in COMPUTED_METRICS:
            for dep in COMPUTED_METRICS[m]["requires"]:
                needed.add(dep)
        elif m in TEMPORAL_METRICS:
            base = TEMPORAL_METRICS[m]["base_metric"]
            # base may itself be a computed metric (e.g. free_cash_flow)
            if base in BASE_METRIC_CONCEPTS:
                needed.add(base)
            elif base in COMPUTED_METRICS:
                for dep in COMPUTED_METRICS[base]["requires"]:
                    needed.add(dep)
    return list(needed)
