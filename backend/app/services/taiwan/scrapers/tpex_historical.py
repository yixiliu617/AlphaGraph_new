"""
TPEx historical monthly-revenue backfill.

Source: https://www.tpex.org.tw/zh-tw/mainboard/listed/month/revenue.html
    Report "上櫃公司營業額及背書保證金額彙總表" (TPEx Operating Revenue
    and Endorsement Guarantee Summary).
    One .xls per (year, month), no wrapper ZIP.

File layout parallels TWSE C04003 exactly (same tabular structure by
industry section + grand totals at the bottom) EXCEPT TPEx's XLS
inserts an empty spacer column at position 1, so every data column
is shifted right by one vs TWSE.

Endpoints:
    Menu:  /zh-tw/mainboard/listed/month/revenue.html (SPA)
    XLS:   /storage/statistic/sales_revenue/O_{YYYYMM}.xls  (上櫃 - TPEx regular)
           /storage/statistic/sales_revenue/U_{YYYYMM}.xls  (興櫃 - Emerging)
    JSON:  POST /www/zh-tw/statistics/salesRevenue  body=date=&id=&response=json

Coverage: 2009-12 onwards (earlier months return HTTP 302).
"""

from __future__ import annotations

import io
import logging
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests
import urllib3

# Same Python-3.13 strict-TLS workaround as TWSE. Public open-data; the
# deterministic filename-based URL makes MITM substitution obvious.
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_XLS_URL_TEMPLATE = (
    "https://www.tpex.org.tw/storage/statistic/sales_revenue/"
    "{prefix}_{yyyymm}.xls"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_COMPANY_ROW_RE = re.compile(r"^\s*(\d{4,6})\s+(\S.*?)\s*$")
_INDUSTRY_ROW_RE = re.compile(r"^\s*\d{1,2}\s+\S")


@dataclass
class TpexRevenueRow:
    ticker: str
    name_zh: str
    fiscal_ym: str
    revenue_twd: int | None
    prior_year_month_twd: int | None
    cumulative_ytd_twd: int | None
    prior_year_ytd_twd: int | None


def fetch_xls_bytes(
    *,
    year: int,
    month: int,
    prefix: str = "O",
    session: Optional[requests.Session] = None,
    cache_dir: Optional[Path] = None,
    timeout: float = 30.0,
) -> bytes:
    """Download one (year, month) TPEx XLS. Disk-caches by filename.

    ``prefix`` selects the market: 'O' = 上櫃 / TPEx regular (default),
    'U' = 興櫃 / Emerging.
    """
    yyyymm = f"{year:04d}{month:02d}"
    url = _XLS_URL_TEMPLATE.format(prefix=prefix, yyyymm=yyyymm)

    if cache_dir is not None:
        cache_path = cache_dir / f"{prefix}_{yyyymm}.xls"
        if cache_path.exists() and _is_valid_xls_header(cache_path.read_bytes()[:8]):
            return cache_path.read_bytes()

    sess = session or requests
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.tpex.org.tw/"}
    # allow_redirects=False catches the 302 that TPEx returns for
    # not-yet-published months; we bubble that up as an error instead of
    # silently following to the homepage.
    r = sess.get(url, headers=headers, timeout=timeout, verify=False, allow_redirects=False)
    if r.status_code != 200:
        raise FileNotFoundError(
            f"TPEx returned {r.status_code} for {prefix}_{yyyymm}.xls "
            f"(likely not published)"
        )

    if cache_dir is not None and _is_valid_xls_header(r.content[:8]):
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{prefix}_{yyyymm}.xls").write_bytes(r.content)

    return r.content


def _is_valid_xls_header(prefix: bytes) -> bool:
    """Legacy .xls (CFB compound document) starts with D0CF11E0 A1B11AE1."""
    return prefix[:4] == b"\xd0\xcf\x11\xe0"


def _to_int(cell) -> int | None:
    if cell is None:
        return None
    if isinstance(cell, float):
        if pd.isna(cell):
            return None
        return int(cell)
    if isinstance(cell, int):
        return cell
    s = str(cell).replace(",", "").strip()
    if not s or s in ("-", "—"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_tpex_revenue_xls(
    xls_bytes: bytes, *, year: int, month: int,
) -> list[TpexRevenueRow]:
    """Parse a TPEx monthly-revenue XLS.

    Column offsets vs TWSE C04003:
      col 0 = {ticker}  {name_zh}
      col 1 = SPACER (always blank)    <-- the TPEx-specific extra column
      col 2 = 上月本月 (previous month)
      col 3 = 本月 (current month)             <-- our revenue
      col 4 = 累計 (YTD)
      col 5 = 上年度本月 (prior year same month)
      col 6 = 上年度累計 (prior year YTD)
    """
    df = pd.read_excel(io.BytesIO(xls_bytes), engine="xlrd", header=None, dtype=str)
    fiscal_ym = f"{year:04d}-{month:02d}"
    rows: list[TpexRevenueRow] = []

    for _, r in df.iterrows():
        col0 = r.iloc[0]
        if not isinstance(col0, str):
            continue
        col0 = col0.strip()
        if not col0:
            continue
        if _INDUSTRY_ROW_RE.match(col0) and not _COMPANY_ROW_RE.match(col0):
            continue
        m = _COMPANY_ROW_RE.match(col0)
        if not m:
            continue
        ticker, name_zh = m.group(1), m.group(2).strip()

        current = _to_int(r.iloc[3]) if len(r) > 3 else None
        prior_ym = _to_int(r.iloc[5]) if len(r) > 5 else None
        ytd = _to_int(r.iloc[4]) if len(r) > 4 else None
        prior_ytd = _to_int(r.iloc[6]) if len(r) > 6 else None

        rows.append(TpexRevenueRow(
            ticker=ticker,
            name_zh=name_zh,
            fiscal_ym=fiscal_ym,
            revenue_twd=current * 1000 if current is not None else None,
            prior_year_month_twd=prior_ym * 1000 if prior_ym is not None else None,
            cumulative_ytd_twd=ytd * 1000 if ytd is not None else None,
            prior_year_ytd_twd=prior_ytd * 1000 if prior_ytd is not None else None,
        ))
    return rows


def iter_year_months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    ey, em = end
    while (y, m) <= (ey, em):
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def rows_to_canonical(
    rows: Iterable[TpexRevenueRow],
    *,
    watchlist: set[str] | None = None,
    market: str = "TPEx",
) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        if watchlist is not None and r.ticker not in watchlist:
            continue
        yoy_pct = None
        if r.revenue_twd and r.prior_year_month_twd:
            yoy_pct = r.revenue_twd / r.prior_year_month_twd - 1
        ytd_pct = None
        if r.cumulative_ytd_twd and r.prior_year_ytd_twd:
            ytd_pct = r.cumulative_ytd_twd / r.prior_year_ytd_twd - 1
        out.append({
            "ticker": r.ticker,
            "market": market,
            "fiscal_ym": r.fiscal_ym,
            "revenue_twd": r.revenue_twd,
            "prior_year_month_twd": r.prior_year_month_twd,
            "yoy_pct": yoy_pct,
            "cumulative_ytd_twd": r.cumulative_ytd_twd,
            "ytd_pct": ytd_pct,
            "mom_pct": None,
        })
    return out


def _attach_mom(rows: list[dict]) -> None:
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    for ticker_rows in by_ticker.values():
        ticker_rows.sort(key=lambda r: r["fiscal_ym"])
        for i in range(1, len(ticker_rows)):
            prev = ticker_rows[i - 1]["revenue_twd"]
            cur = ticker_rows[i]["revenue_twd"]
            if prev and cur:
                ticker_rows[i]["mom_pct"] = cur / prev - 1


def backfill_range(
    *,
    start: tuple[int, int],
    end: tuple[int, int],
    watchlist: set[str],
    cache_dir: Path,
    prefix: str = "O",
    on_progress=None,
) -> list[dict]:
    """Download + parse every month in [start, end]. Returns canonical rows
    filtered to watchlist, with MoM attached across consecutive months."""
    session = requests.Session()
    all_rows: list[dict] = []
    market = "TPEx" if prefix == "O" else "Emerging"
    for year, month in iter_year_months(start, end):
        try:
            xls_bytes = fetch_xls_bytes(
                year=year, month=month, prefix=prefix,
                session=session, cache_dir=cache_dir,
            )
            parsed = parse_tpex_revenue_xls(xls_bytes, year=year, month=month)
            canon = rows_to_canonical(parsed, watchlist=watchlist, market=market)
            all_rows.extend(canon)
            if on_progress:
                on_progress(year, month, len(canon))
        except Exception as exc:
            logger.warning("tpex backfill %04d-%02d failed: %s", year, month, exc)
            if on_progress:
                on_progress(year, month, -1)
    _attach_mom(all_rows)
    return all_rows
