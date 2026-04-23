from backend.app.services.taiwan.validation import (
    validate_monthly_revenue_row,
    ValidationFlag,
)


def test_valid_row_returns_no_flags():
    row = {
        "ticker": "2330", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": 200_000_000_000, "yoy_pct": 0.10, "mom_pct": 0.05,
        "ytd_pct": 0.12,
    }
    flags = validate_monthly_revenue_row(row)
    assert flags == []


def test_negative_revenue_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": -100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.NEGATIVE_REVENUE in flags


def test_absurd_yoy_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2026-03",
        "revenue_twd": 100, "yoy_pct": 15.0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.ABSURD_YOY in flags


def test_future_fiscal_ym_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "2099-12",
        "revenue_twd": 100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.FUTURE_PERIOD in flags


def test_invalid_fiscal_ym_format_flagged():
    row = {
        "ticker": "X", "market": "TWSE", "fiscal_ym": "March 2026",
        "revenue_twd": 100, "yoy_pct": 0, "mom_pct": 0, "ytd_pct": 0,
    }
    flags = validate_monthly_revenue_row(row)
    assert ValidationFlag.INVALID_PERIOD_FORMAT in flags
