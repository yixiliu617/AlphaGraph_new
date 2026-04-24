"""
Unit tests for the TPEx OpenAPI redundancy / cross-check scraper.

Uses the exact shape the live endpoint returns (field names carry
Chinese characters; values are strings even for numerics).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.app.services.taiwan.scrapers.tpex_openapi import (
    _roc_ym_to_ad,
    _to_int_ktwd,
    _to_pct,
    Divergence,
    SyncStats,
    parse_to_canonical,
    sync_with_monthly_revenue,
)
from backend.app.services.taiwan.storage import upsert_monthly_revenue


# ---------------- simple helpers ----------------

@pytest.mark.parametrize("ym,expected", [
    ("11503", "2026-03"),
    ("11412", "2025-12"),
    ("11301", "2024-01"),
    ("abc", None),
    ("115", None),
    ("11513", None),           # month 13 invalid
    ("", None),
])
def test_roc_ym_to_ad(ym, expected):
    assert _roc_ym_to_ad(ym) == expected


def test_to_int_ktwd():
    assert _to_int_ktwd("10,451,593") == 10_451_593
    assert _to_int_ktwd("10451593") == 10_451_593
    assert _to_int_ktwd("-") is None
    assert _to_int_ktwd("") is None
    assert _to_int_ktwd(None) is None


def test_to_pct():
    assert abs(_to_pct("45.19") - 0.4519) < 1e-6
    assert abs(_to_pct("45.19%") - 0.4519) < 1e-6
    assert _to_pct("") is None
    assert _to_pct("999999.99") is None   # sentinel


# ---------------- parser ----------------

_SAMPLE_ROW = {
    "出表日期": "1150417",
    "資料年月": "11503",
    "公司代號": "8299",
    "公司名稱": "群聯",
    "產業別": "半導體",
    "營業收入-當月營收": "10451593",
    "營業收入-上月營收": "8711746",
    "營業收入-去年當月營收": "3613390",
    "營業收入-上月比較增減(%)": "19.9713",
    "營業收入-去年同月增減(%)": "189.2462",
    "累計營業收入-當月累計營收": "10451593",
    "累計營業收入-去年累計營收": "3613390",
    "累計營業收入-前期比較增減(%)": "189.2462",
    "備註": "-",
}


def test_parse_to_canonical_converts_thousand_twd_to_full_twd():
    out = parse_to_canonical([_SAMPLE_ROW], watchlist={"8299"})
    assert len(out) == 1
    r = out[0]
    assert r["ticker"] == "8299"
    assert r["market"] == "TPEx"
    assert r["fiscal_ym"] == "2026-03"
    # 仟元 → 元 (× 1000)
    assert r["revenue_twd"] == 10_451_593_000
    assert r["prior_year_month_twd"] == 3_613_390_000
    assert r["cumulative_ytd_twd"] == 10_451_593_000
    # percentage as decimal (1.0 = 100%)
    assert abs(r["yoy_pct"] - 1.892462) < 1e-4
    assert abs(r["mom_pct"] - 0.199713) < 1e-4


def test_parse_to_canonical_respects_watchlist():
    rows = [_SAMPLE_ROW, {**_SAMPLE_ROW, "公司代號": "9999"}]
    out = parse_to_canonical(rows, watchlist={"8299"})
    assert {r["ticker"] for r in out} == {"8299"}


def test_parse_to_canonical_skips_rows_with_unparseable_ym():
    bad = {**_SAMPLE_ROW, "資料年月": ""}
    assert parse_to_canonical([bad], watchlist={"8299"}) == []


def test_parse_to_canonical_tolerates_missing_fields():
    # 累計營業收入-去年累計營收 missing → prior_year_ytd_twd=None is fine
    row = {k: v for k, v in _SAMPLE_ROW.items() if k != "累計營業收入-去年累計營收"}
    out = parse_to_canonical([row], watchlist={"8299"})
    assert out[0]["revenue_twd"] == 10_451_593_000


# ---------------- sync/cross-check ----------------

def _tpex_row(ticker="8299", ym="2026-03", rev_twd=10_451_593_000):
    return {
        "ticker": ticker, "market": "TPEx", "fiscal_ym": ym,
        "revenue_twd": rev_twd,
        "prior_year_month_twd": 3_613_390_000,
        "cumulative_ytd_twd": rev_twd,
        "yoy_pct": 1.89, "mom_pct": 0.20, "ytd_pct": 1.89,
    }


def _mr_row(ticker="8299", ym="2026-03", rev_twd=10_451_593_000):
    return {
        "ticker": ticker, "market": "TPEx", "fiscal_ym": ym,
        "revenue_twd": rev_twd,
        "prior_year_month_twd": 3_613_390_000,
        "cumulative_ytd_twd": rev_twd,
        "yoy_pct": 1.89, "mom_pct": 0.20, "ytd_pct": 1.89,
    }


def test_sync_empty_parquet_inserts_redundancy_rows(tmp_path):
    """MOPS was down → parquet empty → TPEx API fills in."""
    stats = sync_with_monthly_revenue([_tpex_row()], data_dir=tmp_path)
    assert stats.fetched == 1
    assert stats.inserted == 1
    assert stats.matched == 0
    assert stats.divergent == 0


def test_sync_matching_parquet_records_match_not_insert(tmp_path):
    """MOPS already populated with the identical value → MATCHED."""
    upsert_monthly_revenue([_mr_row()], data_dir=tmp_path)
    stats = sync_with_monthly_revenue([_tpex_row()], data_dir=tmp_path)
    assert stats.inserted == 0
    assert stats.matched == 1
    assert stats.divergent == 0


def test_sync_divergent_values_are_flagged_not_overwritten(tmp_path, caplog):
    """MOPS has value X, TPEx has different value Y → DIVERGENT, but
    we DO NOT overwrite the parquet (cross-source disagreement is a
    data-quality signal for humans, not an automated restatement)."""
    upsert_monthly_revenue([_mr_row(rev_twd=10_000_000_000)], data_dir=tmp_path)
    with caplog.at_level("WARNING"):
        stats = sync_with_monthly_revenue(
            [_tpex_row(rev_twd=10_451_593_000)], data_dir=tmp_path,
        )
    assert stats.inserted == 0
    assert stats.matched == 0
    assert stats.divergent == 1
    assert isinstance(stats.divergences[0], Divergence)
    assert stats.divergences[0].tpex_revenue == 10_451_593_000
    assert stats.divergences[0].stored_revenue == 10_000_000_000
    # Parquet still has the MOPS value, NOT overwritten
    from backend.app.services.taiwan.storage import read_monthly_revenue
    df = read_monthly_revenue(data_dir=tmp_path)
    assert df.iloc[0]["revenue_twd"] == 10_000_000_000
    # WARN was logged
    assert any("DIVERGENT" in rec.message for rec in caplog.records)


def test_sync_mixed_case_partial_missing_partial_matching(tmp_path):
    # Existing: 8299 matches. Missing: 6488 (MOPS-down scenario for that ticker).
    upsert_monthly_revenue([_mr_row(ticker="8299")], data_dir=tmp_path)
    tpex_rows = [
        _tpex_row(ticker="8299", rev_twd=10_451_593_000),
        _tpex_row(ticker="6488", rev_twd=4_139_278_000),
    ]
    stats = sync_with_monthly_revenue(tpex_rows, data_dir=tmp_path)
    assert stats.fetched == 2
    assert stats.matched == 1
    assert stats.inserted == 1
    assert stats.divergent == 0
