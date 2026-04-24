"""
TWSE historical monthly-revenue backfill.

Source: https://www.twse.com.tw/zh/trading/statistics/index04.html
    Report "國內上市公司營業收入彙總表" (Domestic Listed Companies Revenue Summary)
    Report index C04003.
    One legacy .xls per (year, month) inside a .zip, 28 years deep
    (民國 88 = 1999 onward).

Why this exists:
    The MOPS JSON endpoint `/mops/api/t146sb05_detail` caps at 12 months
    per call, so it can't produce >1 year of history for a fresh ticker.
    TWSE's open-data statistics portal has no WAF and serves bulk monthly
    files covering the whole TWSE main board, all the way back to 1999.

    TPEx (上櫃) companies are NOT included here — they come from a
    separate TPEx endpoint (TODO).

Endpoints:
    Manifest:  GET /rwd/zh/statistics/download?type=04&response=json
    ZIP:       GET /staticFiles/inspection/inspection/04/003/YYYYMM_C04003.zip
               where YYYY = AD year, MM = zero-padded month
"""

from __future__ import annotations

import io
import logging
import re
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests
import urllib3

# TWSE's cert chain is missing Subject Key Identifier, which Python 3.13's
# stricter TLS validation rejects even though browsers (and curl) accept it
# fine. We disable verification ONLY for twse.com.tw — the data is public
# open-data, there's no secret payload, and the filename-only URL makes
# MITM substitution loud (wrong filesize, bad ZIP checksum).
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

_MANIFEST_URL = "https://www.twse.com.tw/rwd/zh/statistics/download?type=04&response=json"
_ZIP_URL_TEMPLATE = (
    "https://www.twse.com.tw/staticFiles/inspection/inspection/"
    "04/003/{yyyymm}_C04003.zip"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Ticker row: 4-6 digit code + whitespace + name (non-empty).
_COMPANY_ROW_RE = re.compile(r"^\s*(\d{4,6})\s+(\S.*?)\s*$")
# Industry header: 1-2 digit code + name (we SKIP these).
_INDUSTRY_ROW_RE = re.compile(r"^\s*\d{1,2}\s+\S")


@dataclass
class TwseC04003Row:
    """One parsed XLS row in our canonical shape."""

    ticker: str
    name_zh: str
    fiscal_ym: str           # '2026-01'
    revenue_twd: int | None          # thousand-TWD -> full TWD (× 1000)
    prior_year_month_twd: int | None
    cumulative_ytd_twd: int | None
    prior_year_ytd_twd: int | None


def fetch_zip_bytes(
    *,
    year: int,
    month: int,
    session: Optional[requests.Session] = None,
    cache_dir: Optional[Path] = None,
    timeout: float = 30.0,
) -> bytes:
    """Download one (year, month) report ZIP. Uses a disk cache if supplied."""
    yyyymm = f"{year:04d}{month:02d}"
    url = _ZIP_URL_TEMPLATE.format(yyyymm=yyyymm)

    if cache_dir is not None:
        cache_path = cache_dir / f"{yyyymm}_C04003.zip"
        if cache_path.exists():
            return cache_path.read_bytes()

    sess = session or requests
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.twse.com.tw/"}
    r = sess.get(url, headers=headers, timeout=timeout, verify=False)
    r.raise_for_status()

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{yyyymm}_C04003.zip").write_bytes(r.content)

    return r.content


def _extract_xls_from_zip(zip_bytes: bytes) -> bytes:
    """Return the XLS bytes from inside a single-file TWSE ZIP."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xls_names = [n for n in zf.namelist() if n.lower().endswith(".xls")]
        if not xls_names:
            raise ValueError("No .xls found inside ZIP")
        return zf.read(xls_names[0])


def _to_int(cell) -> int | None:
    """Parse XLS numeric cell into int. Handles str, float, numpy scalars, NaN."""
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


def parse_c04003_xls(xls_bytes: bytes, *, year: int, month: int) -> list[TwseC04003Row]:
    """Parse C04003 XLS into canonical rows.

    Skips: title/header block (rows 0-9), industry headers (1-2 digit codes),
    grand-total rows ('總額 Total', '平均 Average'), and footer notes.
    """
    df = pd.read_excel(io.BytesIO(xls_bytes), engine="xlrd", header=None, dtype=str)
    fiscal_ym = f"{year:04d}-{month:02d}"
    rows: list[TwseC04003Row] = []

    for _, r in df.iterrows():
        col0 = r.iloc[0]
        if not isinstance(col0, str):
            continue
        col0 = col0.strip()
        if not col0:
            continue
        # Skip industry headers (1-2 digit code) and aggregates/notes.
        if _INDUSTRY_ROW_RE.match(col0) and not _COMPANY_ROW_RE.match(col0):
            continue
        m = _COMPANY_ROW_RE.match(col0)
        if not m:
            continue
        ticker, name_zh = m.group(1), m.group(2).strip()

        # Column indices discovered from 202601_C04003.xls:
        #   col 1 = 上月本月 (prev month)
        #   col 2 = 本月 (current month)
        #   col 3 = 累計 (YTD)
        #   col 4 = 上年度本月 (prior year same month)
        #   col 5 = 上年度累計 (prior year YTD)
        current = _to_int(r.iloc[2]) if len(r) > 2 else None
        prior_ym = _to_int(r.iloc[4]) if len(r) > 4 else None
        ytd = _to_int(r.iloc[3]) if len(r) > 3 else None
        prior_ytd = _to_int(r.iloc[5]) if len(r) > 5 else None

        # XLS values are thousand-TWD — convert to full TWD.
        rows.append(
            TwseC04003Row(
                ticker=ticker,
                name_zh=name_zh,
                fiscal_ym=fiscal_ym,
                revenue_twd=current * 1000 if current is not None else None,
                prior_year_month_twd=prior_ym * 1000 if prior_ym is not None else None,
                cumulative_ytd_twd=ytd * 1000 if ytd is not None else None,
                prior_year_ytd_twd=prior_ytd * 1000 if prior_ytd is not None else None,
            )
        )
    return rows


def iter_year_months(start: tuple[int, int], end: tuple[int, int]):
    """Yield (year, month) inclusive from start to end, ascending."""
    y, m = start
    ey, em = end
    while (y, m) <= (ey, em):
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def rows_to_canonical(
    rows: Iterable[TwseC04003Row],
    *,
    watchlist: set[str] | None = None,
    market: str = "TWSE",
) -> list[dict]:
    """Convert TwseC04003Row -> dict rows in our canonical monthly_revenue schema.

    Computes yoy_pct and ytd_pct from raw values; MoM is computed later,
    after all months are stitched together (see backfill_watchlist()).
    """
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
            "mom_pct": None,  # filled by the backfill driver across months
        })
    return out


def _attach_mom(rows: list[dict]) -> None:
    """In-place: fill mom_pct from consecutive months per ticker."""
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
    on_progress=None,
) -> list[dict]:
    """Download + parse every month in [start, end] and return canonical rows
    filtered to `watchlist`, with MoM%% filled from consecutive months.

    Does NOT write to storage — caller upserts via storage.upsert_monthly_revenue
    (so this function is pure and testable).
    """
    session = requests.Session()
    all_rows: list[dict] = []
    for year, month in iter_year_months(start, end):
        try:
            zip_bytes = fetch_zip_bytes(
                year=year, month=month, session=session, cache_dir=cache_dir,
            )
            xls_bytes = _extract_xls_from_zip(zip_bytes)
            parsed = parse_c04003_xls(xls_bytes, year=year, month=month)
            canon = rows_to_canonical(parsed, watchlist=watchlist, market="TWSE")
            all_rows.extend(canon)
            if on_progress:
                on_progress(year, month, len(canon))
        except Exception as exc:
            logger.warning("backfill %04d-%02d failed: %s", year, month, exc)
            if on_progress:
                on_progress(year, month, -1)
    _attach_mom(all_rows)
    return all_rows
