"""
Parser tests for the TPEx historical monthly-revenue backfill.

Fixture: backend/tests/fixtures/taiwan/tpex_O_202601.xls — real XLS
downloaded from tpex.org.tw on 2026-04-24, containing 上櫃公司
operating revenue for January 2026. Re-capture via:
    curl -o backend/tests/fixtures/taiwan/tpex_O_202601.xls \
        https://www.tpex.org.tw/storage/statistic/sales_revenue/O_202601.xls
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.taiwan.scrapers.tpex_historical import (
    _attach_mom,
    iter_year_months,
    parse_tpex_revenue_xls,
    rows_to_canonical,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "taiwan" / "tpex_O_202601.xls"
)


@pytest.fixture
def xls_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_parse_returns_company_rows_only(xls_bytes):
    rows = parse_tpex_revenue_xls(xls_bytes, year=2026, month=1)
    assert len(rows) > 500  # lots of TPEx listed companies
    for r in rows:
        assert r.ticker.isdigit()
        assert 4 <= len(r.ticker) <= 6


def test_parse_phison_row_matches_known_values(xls_bytes):
    """Phison (8299) is a flash-controller company on our watchlist."""
    rows = parse_tpex_revenue_xls(xls_bytes, year=2026, month=1)
    phison = [r for r in rows if r.ticker == "8299"]
    assert len(phison) == 1
    p = phison[0]
    assert p.name_zh == "群聯"
    assert p.fiscal_ym == "2026-01"
    # Values below are the actual TPEx-reported Jan 2026 figures (thousand TWD
    # in the raw file; stored as full TWD here).
    assert p.revenue_twd == 10_451_593_000
    assert p.prior_year_month_twd == 3_613_390_000
    assert p.cumulative_ytd_twd == 10_451_593_000
    assert p.prior_year_ytd_twd == 3_613_390_000


def test_rows_to_canonical_filters_watchlist_and_tags_tpex(xls_bytes):
    rows = parse_tpex_revenue_xls(xls_bytes, year=2026, month=1)
    canon = rows_to_canonical(rows, watchlist={"8299", "6488"}, market="TPEx")
    tickers = {r["ticker"] for r in canon}
    assert tickers == {"8299", "6488"}
    for r in canon:
        assert r["market"] == "TPEx"
        assert r["fiscal_ym"] == "2026-01"
        # revenue computed in TWD (not thousand-TWD)
        assert r["revenue_twd"] > 1_000_000_000  # > 1B TWD is reasonable
        # YoY computed from consecutive columns
        assert r["yoy_pct"] is not None
        # MoM stays None until _attach_mom runs across months
        assert r["mom_pct"] is None


def test_rows_to_canonical_unknown_prefix_tagged_emerging():
    from backend.app.services.taiwan.scrapers.tpex_historical import TpexRevenueRow
    r = TpexRevenueRow(
        ticker="1234", name_zh="Example",
        fiscal_ym="2026-01",
        revenue_twd=100_000_000, prior_year_month_twd=80_000_000,
        cumulative_ytd_twd=100_000_000, prior_year_ytd_twd=80_000_000,
    )
    canon = rows_to_canonical([r], watchlist=None, market="Emerging")
    assert canon[0]["market"] == "Emerging"


def test_attach_mom_computes_from_consecutive_months():
    rows = [
        {"ticker": "8299", "fiscal_ym": "2025-12", "revenue_twd": 100, "mom_pct": None},
        {"ticker": "8299", "fiscal_ym": "2026-01", "revenue_twd": 150, "mom_pct": None},
        {"ticker": "8299", "fiscal_ym": "2026-02", "revenue_twd": 120, "mom_pct": None},
    ]
    _attach_mom(rows)
    assert rows[0]["mom_pct"] is None
    assert rows[1]["mom_pct"] == pytest.approx(0.5)
    assert rows[2]["mom_pct"] == pytest.approx(-0.2)


def test_iter_year_months_inclusive():
    assert list(iter_year_months((2025, 11), (2026, 1))) == [
        (2025, 11), (2025, 12), (2026, 1),
    ]
