"""
Unit tests for the KeywordsQuery-based company-master resolver.

Exercises the pure helpers (`_split_market_sector`, `_parse_ticker_name`)
and the full `resolve_ticker` flow with a mocked MopsClient. No network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.app.services.taiwan.mops_client import MopsFetchResult
from backend.app.services.taiwan.scrapers.company_master import (
    _parse_ticker_name,
    _split_market_sector,
    resolve_ticker,
)


def test_split_market_sector_twse():
    assert _split_market_sector("上市半導體業") == ("TWSE", "半導體業")


def test_split_market_sector_tpex():
    assert _split_market_sector("上櫃電腦及週邊設備業") == ("TPEx", "電腦及週邊設備業")


def test_split_market_sector_emerging():
    assert _split_market_sector("興櫃光電業") == ("Emerging", "光電業")


def test_split_market_sector_public_non_listed():
    assert _split_market_sector("公開發行其他業") == ("Public", "其他業")


def test_split_market_sector_unknown_prefix():
    market, sector = _split_market_sector("海外半導體業")
    assert market == "Unknown"
    assert sector == "海外半導體業"


def test_parse_ticker_name_standard_format():
    t, name = _parse_ticker_name("2330 台灣積體電路製造股份有限公司")
    assert t == "2330"
    assert name == "台灣積體電路製造股份有限公司"


def test_parse_ticker_name_six_digit_ticker():
    t, name = _parse_ticker_name("911001 Example Co.")
    assert t == "911001"
    assert name == "Example Co."


def test_parse_ticker_name_malformed_returns_empty_ticker():
    t, name = _parse_ticker_name("no-ticker-here")
    assert t == ""
    assert name == "no-ticker-here"


def _mock_client_returning(body_text: str, status: int = 200) -> MagicMock:
    c = MagicMock()
    c.post_json.return_value = MopsFetchResult(
        status_code=status,
        text=body_text,
        raw_bytes=body_text.encode("utf-8"),
        used_browser=True,
    )
    return c


def test_resolve_ticker_happy_path():
    body = (
        '{"code":200,"message":"ok","result":{"companyList":['
        '{"title":"上市半導體業","data":[{"url":"#/web/t146sb05?companyId=2330",'
        '"result":"2330 台灣積體電路製造股份有限公司"}]}]}}'
    )
    client = _mock_client_returning(body)
    row = resolve_ticker(client, "2330")
    assert row == {
        "co_id": "2330",
        "name_zh": "台灣積體電路製造股份有限公司",
        "industry_zh": "半導體業",
        "market": "TWSE",
    }


def test_resolve_ticker_returns_none_on_miss():
    body = '{"code":200,"result":{"companyList":[]}}'
    client = _mock_client_returning(body)
    assert resolve_ticker(client, "9999") is None


def test_resolve_ticker_returns_none_on_non_200_status():
    body = '{"code":500,"message":"server error"}'
    client = _mock_client_returning(body, status=500)
    assert resolve_ticker(client, "2330") is None


def test_resolve_ticker_returns_none_on_non_json_body():
    client = _mock_client_returning("<html>blocked</html>")
    assert resolve_ticker(client, "2330") is None


def test_resolve_ticker_picks_matching_ticker_when_multiple_results():
    """KeywordsQuery can return multiple groups / multiple tickers. We must
    match the requested ticker, not pick the first one."""
    body = (
        '{"code":200,"result":{"companyList":[{'
        '"title":"上市半導體業","data":['
        '{"url":"#/web/t146sb05?companyId=2303","result":"2303 聯電"},'
        '{"url":"#/web/t146sb05?companyId=2330","result":"2330 台積電"}]}]}}'
    )
    client = _mock_client_returning(body)
    row = resolve_ticker(client, "2330")
    assert row is not None
    assert row["co_id"] == "2330"
    assert row["name_zh"] == "台積電"
