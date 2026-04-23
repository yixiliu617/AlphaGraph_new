import pandas as pd
import pytest

from backend.app.services.taiwan.amendments import (
    compute_content_hash,
    detect_amendment,
    AmendmentDecision,
)


def test_content_hash_stable_for_equivalent_rows():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "yoy_pct": 0.1}
    row_b = dict(reversed(list(row_a.items())))  # same content, different dict insertion order
    assert compute_content_hash(row_a) == compute_content_hash(row_b)


def test_content_hash_differs_when_value_changes():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "yoy_pct": 0.1}
    row_b = {**row_a, "revenue_twd": 1001}
    assert compute_content_hash(row_a) != compute_content_hash(row_b)


def test_content_hash_ignores_mutable_columns():
    row_a = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000,
             "first_seen_at": "2026-04-01", "last_seen_at": "2026-04-01"}
    row_b = {**row_a, "last_seen_at": "2026-04-10"}
    assert compute_content_hash(row_a) == compute_content_hash(row_b)


def test_detect_amendment_insert_when_no_prior():
    prior_df = pd.DataFrame(columns=["ticker", "fiscal_ym", "revenue_twd", "content_hash"])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000,
               "content_hash": "abc"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.INSERT


def test_detect_amendment_noop_when_hash_matches():
    prior_df = pd.DataFrame([
        {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    ])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.TOUCH_ONLY


def test_detect_amendment_amend_when_hash_differs():
    prior_df = pd.DataFrame([
        {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1000, "content_hash": "abc"}
    ])
    new_row = {"ticker": "2330", "fiscal_ym": "2026-03", "revenue_twd": 1001, "content_hash": "def"}
    assert detect_amendment(prior_df, new_row, key_cols=["ticker", "fiscal_ym"]) \
        == AmendmentDecision.AMEND
