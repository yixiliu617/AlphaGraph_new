"""
Unit tests for the t05st02 material-information scraper.

Uses the real subject-line shapes observed in the April 2026 publication
window so the filter stays honest.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.services.taiwan.mops_client import MopsFetchResult
from backend.app.services.taiwan.scrapers.material_info import (
    MaterialInfoMatch,
    _roc_dt_to_iso,
    extract_fiscal_ym_guess,
    extract_revenue_matches,
    fetch_day,
    looks_like_revenue_subject,
)


# ------------------- keyword filter -------------------

@pytest.mark.parametrize("subj,expect", [
    ("公告本公司2026年3月合併營業額", True),            # AD year + 營業額
    ("公告本公司115年3月份合併營業額", True),            # ROC year + 營業額
    ("台積公司2026年3月營收報告", True),                 # 3月營收 (contains 月營收)
    ("聯發科技115年3月份自結合併營收淨額公告", True),       # 合併營收
    ("公告本公司115年02月份自結財務報表之自結負債比率", False),  # 自結 alone is not enough
    ("公告本公司取得有價證券", False),                     # unrelated
    ("", False),
    ("公告本公司115年02月自結應收帳款餘額", False),         # 自結 alone, no strong token
])
def test_looks_like_revenue_subject(subj, expect):
    assert looks_like_revenue_subject(subj) is expect


# ------------------- fiscal_ym extraction -------------------

@pytest.mark.parametrize("subj,expect", [
    ("台積公司2026年3月營收報告", "2026-03"),             # AD year
    ("聯發科技115年3月份自結合併營收淨額公告", "2026-03"),   # ROC year
    ("公告本公司114年12月合併營業額", "2025-12"),          # ROC year, Dec
    ("no date here", ""),
    ("2099年99月", ""),                                   # out of range month
])
def test_extract_fiscal_ym_guess(subj, expect):
    assert extract_fiscal_ym_guess(subj) == expect


# ------------------- ROC datetime → ISO -------------------

def test_roc_dt_to_iso():
    assert _roc_dt_to_iso("115/04/10", "17:30:27") == "2026-04-10T17:30:27"
    assert _roc_dt_to_iso("100/01/01", "00:00:01") == "2011-01-01T00:00:01"
    # bad input — returns pass-through
    assert _roc_dt_to_iso("not-a-date", "x") == "not-a-dateTx"


# ------------------- filter over raw t05st02 rows -------------------

def _raw_row(date_, time_, ticker, name, subject, params=None):
    return [date_, time_, ticker, name, subject, params or {}]


def test_extract_revenue_matches_filters_and_normalizes():
    rows = [
        _raw_row("115/04/10", "13:50:36", "2330", "台積電",
                 "台積公司2026年3月營收報告"),
        _raw_row("115/04/10", "16:55:44", "2454", "聯發科",
                 "聯發科技115年3月份自結合併營收淨額公告"),
        # non-watchlist ticker — ignored
        _raw_row("115/04/10", "12:00:00", "9999", "SomeCo",
                 "公告本公司2026年3月合併營業額"),
        # watchlist ticker but non-revenue subject — ignored
        _raw_row("115/04/10", "10:00:00", "2330", "台積電",
                 "公告本公司取得有價證券"),
    ]
    matches = extract_revenue_matches(rows, watchlist={"2330", "2454"})
    assert len(matches) == 2

    tsmc = next(m for m in matches if m.ticker == "2330")
    assert tsmc.name_zh == "台積電"
    assert tsmc.announcement_datetime == "2026-04-10T13:50:36"
    assert tsmc.fiscal_ym_guess == "2026-03"
    # parameters_json is serialised (empty dict in this fixture)
    assert isinstance(tsmc.parameters_json, str)

    mtk = next(m for m in matches if m.ticker == "2454")
    assert mtk.fiscal_ym_guess == "2026-03"
    assert mtk.announcement_datetime == "2026-04-10T16:55:44"


def test_extract_revenue_matches_handles_short_rows_gracefully():
    rows = [
        ["115/04/10", "10:00:00"],              # too short
        ["115/04/10", "10:00:00", "2330", ""],  # missing subject
        ["115/04/10", "10:00:00", "2330", "台積電", "公告本公司2026年3月合併營業額"],
    ]
    matches = extract_revenue_matches(rows, watchlist={"2330"})
    assert len(matches) == 1


# ------------------- fetch_day (mocked MopsClient) -------------------

def _mock_client_returning(body_text: str, status: int = 200) -> MagicMock:
    c = MagicMock()
    c.post_json.return_value = MopsFetchResult(
        status_code=status,
        text=body_text,
        raw_bytes=body_text.encode("utf-8"),
        used_browser=True,
    )
    return c


def test_fetch_day_happy_path():
    body = json.dumps({
        "code": 200,
        "result": {"data": [
            ["115/04/10", "13:50:36", "2330", "台積電", "台積公司2026年3月營收報告",
             {"parameters": {"companyId": "2330"}, "apiName": "t05st01_detail"}]
        ]},
    }, ensure_ascii=False)
    client = _mock_client_returning(body)
    rows = fetch_day(client, roc_year=115, month=4, day=10)
    assert len(rows) == 1
    assert rows[0][2] == "2330"
    # Validate MopsClient was called with the ROC date payload shape.
    client.post_json.assert_called_once()
    args, kwargs = client.post_json.call_args
    assert args[0] == "/mops/api/t05st02"
    assert args[1] == {"year": "115", "month": "04", "day": "10"}


def test_fetch_day_returns_empty_on_non_200():
    client = _mock_client_returning("{}", status=503)
    assert fetch_day(client, roc_year=115, month=4, day=10) == []


def test_fetch_day_returns_empty_on_api_error_code():
    body = json.dumps({"code": 500, "message": "fail"})
    client = _mock_client_returning(body)
    assert fetch_day(client, roc_year=115, month=4, day=10) == []


def test_fetch_day_returns_empty_on_non_json_body():
    client = _mock_client_returning("<html>blocked</html>")
    assert fetch_day(client, roc_year=115, month=4, day=10) == []
