"""
MOPS company-master scraper.

Scrapes the full listed-company registry from MOPS and writes it to
backend/data/taiwan/_registry/mops_company_master.parquet. Run once a month.

Endpoints:
  TWSE main board: POST https://mops.twse.com.tw/mops/web/ajax_t51sb01
                   form: step=1&TYPEK=sii
  TPEx OTC:        POST https://mops.twse.com.tw/mops/web/ajax_t51sb01
                   form: step=1&TYPEK=otc
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
from bs4 import BeautifulSoup

from backend.app.services.taiwan.mops_client import MopsClient
from backend.app.services.taiwan.registry import load_mops_master, save_mops_master

_URL = "https://mops.twse.com.tw/mops/web/ajax_t51sb01"


def parse_company_master_html(html: str, *, market: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for table in soup.select("table"):
        # The company-list tables have a header with 公司代號 / 公司名稱.
        first_row = table.find("tr")
        if not first_row:
            continue
        headers = [th.get_text(strip=True) for th in first_row.find_all(["th", "td"])]
        if "公司代號" not in headers or "公司名稱" not in headers:
            continue
        idx_id = headers.index("公司代號")
        idx_name = headers.index("公司名稱")
        idx_industry = headers.index("產業類別") if "產業類別" in headers else None
        for tr in table.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) <= idx_name:
                continue
            rows.append({
                "co_id": cells[idx_id].strip(),
                "name_zh": cells[idx_name].strip(),
                "industry_zh": (cells[idx_industry].strip() if idx_industry is not None else ""),
                "market": market,
            })
    return rows


def scrape_company_master(client: MopsClient) -> int:
    """Scrape both markets; upsert into _registry/mops_company_master.parquet.
    Returns number of rows written."""
    now = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    for market_code, market_label in (("sii", "TWSE"), ("otc", "TPEx")):
        result = client.post(_URL, data={"step": "1", "TYPEK": market_code})
        if result.status_code != 200:
            continue
        rows = parse_company_master_html(result.text, market=market_label)
        for r in rows:
            r["last_seen_at"] = now
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    save_mops_master(df)
    return len(df)
