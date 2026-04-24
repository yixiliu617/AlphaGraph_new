"""
Parser tests for the JSON-era monthly-revenue scraper.

The fixture `backend/tests/fixtures/taiwan/mops_t146sb05_detail_2330.json`
was captured from a live MOPS call to `/mops/api/t146sb05_detail` with
`{"company_id": "2330"}` on 2026-04-24.

When MOPS changes the response shape, re-capture the fixture via
`python tools/mops_fetch_detail_2330.py` and update this test's expected
values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services.taiwan.scrapers.monthly_revenue import (
    _parse_int,
    _parse_pct,
    _roc_ym_to_ad,
    parse_detail_rows,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "taiwan" / "mops_t146sb05_detail_2330.json"


@pytest.fixture
def tsmc_detail_body() -> dict:
    with FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)


def test_roc_to_ad_year_conversion():
    assert _roc_ym_to_ad("115", "3") == "2026-03"
    assert _roc_ym_to_ad("114", "12") == "2025-12"
    assert _roc_ym_to_ad("100", "1") == "2011-01"
    assert _roc_ym_to_ad("115", "13") is None  # bad month
    assert _roc_ym_to_ad("abc", "3") is None


def test_parse_int_strips_thousand_separator():
    assert _parse_int("1,234,567") == 1_234_567
    assert _parse_int("") is None
    assert _parse_int("—") is None
    assert _parse_int("-") is None
    assert _parse_int("415,191,699") == 415_191_699


def test_parse_pct_handles_percent_and_unicode_minus():
    assert abs(_parse_pct("45.19%") - 0.4519) < 1e-6
    assert abs(_parse_pct("45.19") - 0.4519) < 1e-6
    assert abs(_parse_pct("−12.5%") - (-0.125)) < 1e-6  # en-dash minus
    assert _parse_pct("") is None
    assert _parse_pct("-") is None


def test_parse_pct_sentinel_is_none():
    """MOPS returns 999999.99 for overflow / divide-by-zero — must map to None."""
    assert _parse_pct("999999.99%") is None
    assert _parse_pct("-999999.99") is None


def test_parse_detail_rows_returns_12_sorted_oldest_first(tsmc_detail_body):
    data = tsmc_detail_body["result"]["data"]
    rows = parse_detail_rows(data, ticker="2330", market="TWSE")
    assert len(rows) == 12
    # oldest-first after sorting
    assert rows[0]["fiscal_ym"] < rows[-1]["fiscal_ym"]
    # All carry ticker/market
    assert all(r["ticker"] == "2330" for r in rows)
    assert all(r["market"] == "TWSE" for r in rows)


def test_parse_detail_rows_converts_thousand_twd_to_full_twd(tsmc_detail_body):
    data = tsmc_detail_body["result"]["data"]
    rows = parse_detail_rows(data, ticker="2330", market="TWSE")
    latest = rows[-1]
    # March 2026: 415,191,699 thousand TWD -> 415,191,699,000 full TWD
    assert latest["fiscal_ym"] == "2026-03"
    assert latest["revenue_twd"] == 415_191_699_000
    assert latest["prior_year_month_twd"] == 285_956_830_000
    assert latest["cumulative_ytd_twd"] == 1_134_103_440_000


def test_parse_detail_rows_yoy_ytd_as_decimal(tsmc_detail_body):
    data = tsmc_detail_body["result"]["data"]
    rows = parse_detail_rows(data, ticker="2330", market="TWSE")
    latest = rows[-1]
    assert abs(latest["yoy_pct"] - 0.4519) < 1e-4
    assert abs(latest["ytd_pct"] - 0.3513) < 1e-4


def test_parse_detail_rows_mom_is_computed_locally(tsmc_detail_body):
    """API doesn't expose MoM%; scraper computes it from consecutive months."""
    data = tsmc_detail_body["result"]["data"]
    rows = parse_detail_rows(data, ticker="2330", market="TWSE")

    # oldest row has no MoM (no prior)
    assert rows[0]["mom_pct"] is None

    # Mar 2026 MoM = 415,191,699 / 317,656,613 - 1 ≈ +30.7%
    mar = next(r for r in rows if r["fiscal_ym"] == "2026-03")
    assert mar["mom_pct"] is not None
    assert abs(mar["mom_pct"] - (415_191_699 / 317_656_613 - 1)) < 1e-6


def test_parse_detail_rows_tolerates_empty_input():
    assert parse_detail_rows([], ticker="2330", market="TWSE") == []


def test_parse_detail_rows_skips_malformed_rows():
    data = [
        ["115", "3", "100,000", "90,000", "11.11%", "100,000", "90,000", "11.11%"],
        ["bad", "zz"],  # too short / unparseable
        ["115", "2", "-", "-", "-", "-", "-", "-"],  # dash-only row is kept but values None
    ]
    rows = parse_detail_rows(data, ticker="2330", market="TWSE")
    assert len(rows) == 2
    dash_row = next(r for r in rows if r["fiscal_ym"] == "2026-02")
    assert dash_row["revenue_twd"] is None
    assert dash_row["yoy_pct"] is None
