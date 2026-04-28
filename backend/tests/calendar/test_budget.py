from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from backend.app.services.calendar.enrichment.budget import (
    DAILY_CAP_USD, COST_PER_GEMINI_CALL_USD, remaining_budget_today,
)


def _make_events_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_remaining_budget_when_zero_spent():
    df = _make_events_df([])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == DAILY_CAP_USD


def test_remaining_budget_subtracts_today_spend_only():
    today = pd.Timestamp.now(tz="UTC").normalize()
    yesterday = today - pd.Timedelta(days=1)
    df = _make_events_df([
        {"enrichment_b_attempted_at": yesterday, "enrichment_b_cost_usd": 0.50},
        {"enrichment_b_attempted_at": today,     "enrichment_b_cost_usd": 0.30},
        {"enrichment_b_attempted_at": today,     "enrichment_b_cost_usd": 0.10},
    ])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == pytest.approx(DAILY_CAP_USD - 0.40)


def test_remaining_budget_clamps_to_zero():
    today = pd.Timestamp.now(tz="UTC").normalize()
    df = _make_events_df([
        {"enrichment_b_attempted_at": today, "enrichment_b_cost_usd": 5.00},  # over cap
    ])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == 0.0


def test_remaining_budget_handles_missing_columns():
    """Empty/legacy parquets without enrichment_b_* columns should not crash."""
    df = pd.DataFrame(columns=["ticker"])
    with patch("backend.app.services.calendar.enrichment.budget.read_events",
               return_value=df):
        assert remaining_budget_today() == DAILY_CAP_USD


def test_per_call_cost_under_cap():
    """A single Gemini call must fit comfortably under the cap."""
    assert COST_PER_GEMINI_CALL_USD <= DAILY_CAP_USD / 10
