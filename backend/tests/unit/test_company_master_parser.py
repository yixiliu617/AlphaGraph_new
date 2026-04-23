"""
Unit tests for the MOPS company-master parser. Uses a small synthetic HTML
snippet matching the real MOPS table layout so no network traffic is required.
"""

from backend.app.services.taiwan.scrapers.company_master import parse_company_master_html


SAMPLE_HTML = """
<html><body>
<table class="hasBorder">
  <tr><th>公司代號</th><th>公司名稱</th><th>產業類別</th></tr>
  <tr><td>2330</td><td>台積電</td><td>半導體業</td></tr>
  <tr><td>2454</td><td>聯發科</td><td>半導體業</td></tr>
  <tr><td>2317</td><td>鴻海</td><td>其他電子業</td></tr>
</table>
</body></html>
"""


def test_parse_returns_one_row_per_company():
    rows = parse_company_master_html(SAMPLE_HTML, market="TWSE")
    assert len(rows) == 3
    tsmc = next(r for r in rows if r["co_id"] == "2330")
    assert tsmc["name_zh"] == "台積電"
    assert tsmc["industry_zh"] == "半導體業"
    assert tsmc["market"] == "TWSE"


def test_parse_empty_html_returns_empty_list():
    assert parse_company_master_html("<html><body></body></html>", market="TWSE") == []


def test_parse_trims_whitespace():
    html = """<table class="hasBorder">
      <tr><th>公司代號</th><th>公司名稱</th><th>產業類別</th></tr>
      <tr><td>  2330  </td><td>  台積電  </td><td>  半導體業  </td></tr>
    </table>"""
    rows = parse_company_master_html(html, market="TWSE")
    assert rows[0]["co_id"] == "2330"
    assert rows[0]["name_zh"] == "台積電"
