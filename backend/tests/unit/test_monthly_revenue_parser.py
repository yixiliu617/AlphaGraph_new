"""
Parser tests with a synthetic MOPS monthly-revenue response. The real endpoint
returns a wide HTML table; we shape a small representative sample.
"""

from backend.app.services.taiwan.scrapers.monthly_revenue import (
    parse_monthly_revenue_html,
)


SAMPLE_HTML = """
<html><body>
<table>
  <tr><th>公司代號</th><th>公司名稱</th>
      <th>當月營收</th>
      <th>上月營收</th>
      <th>去年當月營收</th>
      <th>上月比較增減(%)</th>
      <th>去年同月增減(%)</th>
      <th>當月累計營收</th>
      <th>去年累計營收</th>
      <th>前期比較增減(%)</th>
  </tr>
  <tr><td>2330</td><td>台積電</td>
      <td>200,000,000</td>
      <td>190,000,000</td>
      <td>150,000,000</td>
      <td>5.26</td>
      <td>33.33</td>
      <td>500,000,000</td>
      <td>400,000,000</td>
      <td>25.00</td>
  </tr>
</table>
</body></html>
"""


def test_parse_extracts_one_row_per_company():
    rows = parse_monthly_revenue_html(SAMPLE_HTML, market="TWSE", year=2026, month=3)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "2330"
    assert r["market"] == "TWSE"
    assert r["fiscal_ym"] == "2026-03"
    assert r["revenue_twd"] == 200_000_000
    assert r["prior_year_month_twd"] == 150_000_000
    assert r["cumulative_ytd_twd"] == 500_000_000
    assert abs(r["mom_pct"] - 0.0526) < 1e-4
    assert abs(r["yoy_pct"] - 0.3333) < 1e-4
    assert abs(r["ytd_pct"] - 0.25) < 1e-4


def test_parse_handles_thousands_separator_and_percent():
    html = SAMPLE_HTML.replace("200,000,000", "1,234,567,890")
    rows = parse_monthly_revenue_html(html, market="TWSE", year=2026, month=3)
    assert rows[0]["revenue_twd"] == 1_234_567_890


def test_parse_empty_returns_empty():
    assert parse_monthly_revenue_html("<html></html>", market="TWSE", year=2026, month=3) == []
