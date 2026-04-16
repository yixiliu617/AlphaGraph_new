"""
Data quality rules — a declarative list of invariants every calculated-layer
DataFrame must satisfy.

Each rule is a pure function from DataFrame → violating rows.  Rules never
mutate the input and never raise; they report violations as structured data
so the runner can decide whether to warn, fail, or cross-reference against
the known-exceptions list.

To add a new check:
    1. Append a Rule to RULES
    2. Write its `check` as `lambda df: df[<boolean_mask>]`
    3. Choose severity: "fail" blocks the build, "warn" surfaces in the report
    4. If the check needs cross-row logic (e.g. YoY ratios), use a named
       function instead of a lambda so it's testable in isolation

Rules are intentionally kept small and composable — one invariant per Rule.
Do not create "mega rules" that test three different things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np
import pandas as pd

Severity = Literal["warn", "fail"]


# ---------------------------------------------------------------------------
# Rule dataclass
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """One data quality invariant.

    Attributes
    ----------
    name:
        Snake_case identifier used in reports and exceptions.
        Must be unique across RULES.
    severity:
        "fail"  — block the build if violated (caller decides how)
        "warn"  — surface in the quality report but allow the build to proceed
    description:
        Human-readable one-liner. Appears in the build report next to the count.
    applies_to:
        Columns the rule needs. Rule is skipped if any are missing from the DF.
    check:
        Callable(df) -> DataFrame of VIOLATING rows. Empty = passed.
        Must preserve at least `end_date` in the returned frame so messages
        can cite the specific period.
    message:
        Callable(row) -> str used to format each violation for human readers.
    """

    name: str
    severity: Severity
    description: str
    applies_to: list[str]
    check: Callable[[pd.DataFrame], pd.DataFrame]
    message: Callable[[pd.Series], str]


# ---------------------------------------------------------------------------
# Helper predicates (kept as named fns when the logic spans rows)
# ---------------------------------------------------------------------------

def _detect_share_count_cliff(df: pd.DataFrame, col: str, ratio_threshold: float = 5.0) -> pd.DataFrame:
    """
    Flag rows where share count jumps (or drops) more than `ratio_threshold`
    vs the prior quarter. Legit stock splits will trigger this; that's the
    point — the runner cross-references against `exceptions.py` to suppress
    known splits, leaving only unexplained cliffs.
    """
    if col not in df.columns:
        return df.iloc[0:0]
    sorted_df = df.sort_values("end_date").copy()
    prev = sorted_df[col].shift(1)
    ratio = np.where(
        (prev > 0) & (sorted_df[col] > 0),
        np.maximum(sorted_df[col] / prev, prev / sorted_df[col]),
        np.nan,
    )
    sorted_df["_ratio"] = ratio
    return sorted_df[sorted_df["_ratio"] >= ratio_threshold].drop(columns=["_ratio"])


def _detect_revenue_cliff(df: pd.DataFrame, ratio_threshold: float = 5.0) -> pd.DataFrame:
    """
    Flag rows where revenue jumps >5x or drops to <20% of the prior quarter.
    Catches extraction bugs (pulling an annual value into a quarterly row) and
    genuine discontinuities (acquisitions, business restatements).
    """
    if "revenue" not in df.columns:
        return df.iloc[0:0]
    sorted_df = df.sort_values("end_date").copy()
    prev = sorted_df["revenue"].shift(1)
    ratio = np.where(
        (prev > 0) & (sorted_df["revenue"] > 0),
        np.maximum(sorted_df["revenue"] / prev, prev / sorted_df["revenue"]),
        np.nan,
    )
    sorted_df["_ratio"] = ratio
    return sorted_df[sorted_df["_ratio"] >= ratio_threshold].drop(columns=["_ratio"])


def _detect_margin_identity_break(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cross-metric consistency: gross_profit should equal revenue - cost_of_revenue
    within a small tolerance. If it doesn't, one of the XBRL concepts is
    pulling a wrong value or they're from different dimension axes.
    """
    need = {"revenue", "cost_of_revenue", "gross_profit"}
    if not need.issubset(df.columns):
        return df.iloc[0:0]
    sub = df.dropna(subset=list(need)).copy()
    if sub.empty:
        return sub
    implied = sub["revenue"] - sub["cost_of_revenue"]
    pct_diff = (sub["gross_profit"] - implied).abs() / sub["revenue"].abs().replace(0, np.nan)
    return sub[pct_diff > 0.02]  # >2% gap is suspicious


# ---------------------------------------------------------------------------
# The rule list — single source of truth
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    # -- Basic sanity: share counts ------------------------------------------
    Rule(
        name="shares_basic_positive",
        severity="fail",
        description="shares_basic must be > 0",
        applies_to=["shares_basic"],
        check=lambda df: df[df["shares_basic"].notna() & (df["shares_basic"] <= 0)],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: shares_basic={r['shares_basic']}",
    ),
    Rule(
        name="shares_diluted_positive",
        severity="fail",
        description="shares_diluted must be > 0",
        applies_to=["shares_diluted"],
        check=lambda df: df[df["shares_diluted"].notna() & (df["shares_diluted"] <= 0)],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: shares_diluted={r['shares_diluted']}",
    ),

    # -- Sign consistency: EPS vs net income ---------------------------------
    Rule(
        name="eps_basic_sign_matches_net_income",
        severity="fail",
        description="eps_basic must have the same sign as net_income",
        applies_to=["net_income", "eps_basic"],
        check=lambda df: df[
            df[["net_income", "eps_basic"]].notna().all(axis=1)
            & (df["net_income"] * df["eps_basic"] < 0)
        ],
        message=lambda r: (
            f"{r['end_date'].strftime('%Y-%m-%d')}: "
            f"NI={r['net_income']:.0f}  eps_basic={r['eps_basic']:.2f}"
        ),
    ),
    Rule(
        name="eps_diluted_sign_matches_net_income",
        severity="fail",
        description="eps_diluted must have the same sign as net_income",
        applies_to=["net_income", "eps_diluted"],
        check=lambda df: df[
            df[["net_income", "eps_diluted"]].notna().all(axis=1)
            & (df["net_income"] * df["eps_diluted"] < 0)
        ],
        message=lambda r: (
            f"{r['end_date'].strftime('%Y-%m-%d')}: "
            f"NI={r['net_income']:.0f}  eps_diluted={r['eps_diluted']:.2f}"
        ),
    ),

    # -- Range checks: margins -----------------------------------------------
    Rule(
        name="gross_margin_in_range",
        severity="warn",
        description="gross_margin_pct should be between -50% and 100%",
        applies_to=["gross_margin_pct"],
        check=lambda df: df[
            df["gross_margin_pct"].notna()
            & ((df["gross_margin_pct"] < -50) | (df["gross_margin_pct"] > 100))
        ],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: gross_margin={r['gross_margin_pct']:.1f}%",
    ),
    Rule(
        name="operating_margin_in_range",
        severity="warn",
        description="operating_margin_pct should be between -200% and 100%",
        applies_to=["operating_margin_pct"],
        check=lambda df: df[
            df["operating_margin_pct"].notna()
            & ((df["operating_margin_pct"] < -200) | (df["operating_margin_pct"] > 100))
        ],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: op_margin={r['operating_margin_pct']:.1f}%",
    ),
    Rule(
        name="net_margin_in_range",
        severity="warn",
        description="net_margin_pct should be between -500% and 100%",
        applies_to=["net_margin_pct"],
        check=lambda df: df[
            df["net_margin_pct"].notna()
            & ((df["net_margin_pct"] < -500) | (df["net_margin_pct"] > 100))
        ],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: net_margin={r['net_margin_pct']:.1f}%",
    ),

    # -- Range checks: basic sanity ------------------------------------------
    Rule(
        name="revenue_non_negative",
        severity="warn",
        description="revenue should be >= 0 (negative revenue usually indicates an extraction bug)",
        applies_to=["revenue"],
        check=lambda df: df[df["revenue"].notna() & (df["revenue"] < 0)],
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: revenue={r['revenue']:.0f}",
    ),

    # -- Cross-metric identity checks ----------------------------------------
    Rule(
        name="gross_profit_identity",
        severity="warn",
        description="gross_profit should equal revenue - cost_of_revenue within 2%",
        applies_to=["revenue", "cost_of_revenue", "gross_profit"],
        check=_detect_margin_identity_break,
        message=lambda r: (
            f"{r['end_date'].strftime('%Y-%m-%d')}: "
            f"gross_profit={r['gross_profit']:.0f} vs implied "
            f"{r['revenue'] - r['cost_of_revenue']:.0f}"
        ),
    ),

    # -- Temporal stability: share count cliffs (detects missed splits) ------
    Rule(
        name="shares_basic_no_cliff",
        severity="warn",
        description="shares_basic shouldn't jump/drop >5x between quarters (likely a missed split)",
        applies_to=["shares_basic"],
        check=lambda df: _detect_share_count_cliff(df, "shares_basic"),
        message=lambda r: (
            f"{r['end_date'].strftime('%Y-%m-%d')}: "
            f"shares_basic cliff — check for stock split"
        ),
    ),
    Rule(
        name="shares_diluted_no_cliff",
        severity="warn",
        description="shares_diluted shouldn't jump/drop >5x between quarters (likely a missed split)",
        applies_to=["shares_diluted"],
        check=lambda df: _detect_share_count_cliff(df, "shares_diluted"),
        message=lambda r: (
            f"{r['end_date'].strftime('%Y-%m-%d')}: "
            f"shares_diluted cliff — check for stock split"
        ),
    ),

    # -- Temporal stability: revenue cliffs ----------------------------------
    Rule(
        name="revenue_no_cliff",
        severity="warn",
        description="revenue shouldn't jump >5x between quarters (acquisition or extraction bug)",
        applies_to=["revenue"],
        check=_detect_revenue_cliff,
        message=lambda r: f"{r['end_date'].strftime('%Y-%m-%d')}: revenue cliff vs prior quarter",
    ),
]
