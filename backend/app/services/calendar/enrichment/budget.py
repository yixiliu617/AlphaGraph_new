"""Daily LLM-spend cap for Method B (Gemini-grounded enrichment).

Tracks cumulative Gemini cost per event in events.parquet's
`enrichment_b_cost_usd` column; the budget guard sums today's UTC spend
and refuses further calls once the cap is reached.

Cap is enforced cooperatively: the orchestrator calls remaining_budget_today()
before each Gemini invocation. Bypassing the helper bypasses the cap.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import logging

import pandas as pd

from backend.app.services.calendar.storage import read_events

logger = logging.getLogger(__name__)

DAILY_CAP_USD: float = 1.00
# Gemini Flash with grounding, ~3KB prompt + ~500B output, refined after first
# day of real billing. One call is well under 1/10 of the daily cap so the
# orchestrator can make several attempts even mid-day.
COST_PER_GEMINI_CALL_USD: float = 0.025


def remaining_budget_today() -> float:
    """Return USD remaining in today's enrichment-B budget.

    Reads events.parquet, sums enrichment_b_cost_usd for rows whose
    enrichment_b_attempted_at falls on today's UTC date. Returns
    max(0, DAILY_CAP_USD - spent_today). Resilient to missing columns
    and empty parquets."""
    df = read_events()
    if df.empty:
        return DAILY_CAP_USD
    if "enrichment_b_attempted_at" not in df.columns or "enrichment_b_cost_usd" not in df.columns:
        return DAILY_CAP_USD

    today_start = pd.Timestamp.now(tz="UTC").normalize()
    mask = pd.to_datetime(df["enrichment_b_attempted_at"], utc=True, errors="coerce") >= today_start
    spent_today = pd.to_numeric(
        df.loc[mask, "enrichment_b_cost_usd"], errors="coerce",
    ).fillna(0.0).sum()
    return max(0.0, DAILY_CAP_USD - float(spent_today))
