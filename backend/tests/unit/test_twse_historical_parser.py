"""
Parser tests for the TWSE C04003 historical monthly-revenue backfill.

The fixture `backend/tests/fixtures/taiwan/twse_c04003_202601.zip` is the
real ZIP downloaded from twse.com.tw on 2026-04-24, containing
`20202601.XLS`. When TWSE changes the format, re-download via
`tools/twse_backfill.py --start 2026-01 --end 2026-01 --data-dir /tmp/x`
and copy from the cache directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services.taiwan.scrapers.twse_historical import (
    _attach_mom,
    _extract_xls_from_zip,
    iter_year_months,
    parse_c04003_xls,
    rows_to_canonical,
)

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "taiwan" / "twse_c04003_202601.zip"
)


@pytest.fixture
def xls_bytes() -> bytes:
    zip_bytes = FIXTURE.read_bytes()
    return _extract_xls_from_zip(zip_bytes)


def test_extract_xls_returns_bytes():
    zb = FIXTURE.read_bytes()
    xls = _extract_xls_from_zip(zb)
    # .xls (CFB / compound document) files start with 0xD0CF11E0
    assert xls[:4] == b"\xd0\xcf\x11\xe0"


def test_parse_returns_company_rows_only(xls_bytes):
    rows = parse_c04003_xls(xls_bytes, year=2026, month=1)
    # All rows have 4-6 digit tickers — never industry headers (1-2 digits).
    for r in rows:
        assert r.ticker.isdigit()
        assert 4 <= len(r.ticker) <= 6, f"ticker '{r.ticker}' looks like an industry header"


def test_parse_tsmc_row_matches_known_values(xls_bytes):
    rows = parse_c04003_xls(xls_bytes, year=2026, month=1)
    tsmc = [r for r in rows if r.ticker == "2330"]
    assert len(tsmc) == 1
    t = tsmc[0]
    assert t.name_zh == "台積電"
    assert t.fiscal_ym == "2026-01"
    # XLS values are thousand-TWD; scraper converts to full TWD.
    assert t.revenue_twd == 401_255_128_000
    assert t.prior_year_month_twd == 293_288_038_000
    assert t.cumulative_ytd_twd == 401_255_128_000
    assert t.prior_year_ytd_twd == 293_288_038_000


def test_rows_to_canonical_filters_watchlist_and_computes_yoy(xls_bytes):
    rows = parse_c04003_xls(xls_bytes, year=2026, month=1)
    canon = rows_to_canonical(rows, watchlist={"2330"}, market="TWSE")
    assert len(canon) == 1
    tsmc = canon[0]
    assert tsmc["ticker"] == "2330"
    assert tsmc["market"] == "TWSE"
    assert abs(tsmc["yoy_pct"] - (401_255_128 / 293_288_038 - 1)) < 1e-6
    # YTD is the same as monthly for January -> YoY and ytd_pct equal
    assert abs(tsmc["yoy_pct"] - tsmc["ytd_pct"]) < 1e-6
    assert tsmc["mom_pct"] is None  # filled later across months


def test_attach_mom_computes_from_consecutive_months():
    rows = [
        {"ticker": "2330", "fiscal_ym": "2025-12", "revenue_twd": 100, "mom_pct": None},
        {"ticker": "2330", "fiscal_ym": "2026-01", "revenue_twd": 150, "mom_pct": None},
        {"ticker": "2330", "fiscal_ym": "2026-02", "revenue_twd": 120, "mom_pct": None},
    ]
    _attach_mom(rows)
    # Oldest stays None
    assert rows[0]["mom_pct"] is None
    assert rows[1]["mom_pct"] == pytest.approx(0.5)
    assert rows[2]["mom_pct"] == pytest.approx(-0.2)


def test_iter_year_months_inclusive_range():
    got = list(iter_year_months((2025, 11), (2026, 2)))
    assert got == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_iter_year_months_single_month():
    assert list(iter_year_months((2026, 3), (2026, 3))) == [(2026, 3)]
