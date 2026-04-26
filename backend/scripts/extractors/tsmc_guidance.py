"""
TSMC quarterly guidance (業績展望) extractor.

Each /chinese/quarterly-results/{YYYY}/q{N} page renders a 3-column
table with actuals + guidance, like:

                            1Q26       2Q26
                            實際數    業績展望     業績展望
  營業收入淨額 (美金十億元)   35.90   34.6-35.8   39.0-40.2
  平均匯率 (美元兌新台幣)     31.59   31.6        31.7
  營業毛利率                 66.2%  63.0%-65.0%  65.5%-67.5%
  營業淨利率                 58.1%  54.0%-56.0%  56.5%-58.5%

Column 1 = actuals for the just-ended quarter (current page period).
Column 2 = the ORIGINAL guidance for that same quarter (set 3 months
           earlier on the previous quarter's page).
Column 3 = fresh guidance for the NEXT quarter — the headline news.

This is forward-looking data that lives ONLY on the page (not in any
PDF). Critical for any "TSMC guidance vs actual" backtest.

Layered output:
  BRONZE: backend/data/financials/raw/{ticker}/{year}/{Q}/guidance_page.html
  SILVER: backend/data/financials/guidance/{ticker}.parquet  (long format)

Silver schema (one row per metric × bound × guidance-issued page):
  ticker, period_end (the period being talked about),
  period_label, metric (revenue / gross_margin / operating_margin /
  usd_ntd_avg_rate), bound (actual / low / high / point), value, unit,
  guidance_issued_at (date — the page's quarter-end), source, extracted_at.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "guidance"


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"\b(\d)Q(\d{2})\b")


def parse_period_label(label: str) -> tuple[str, date]:
    m = _PERIOD_RE.match(label)
    if not m:
        raise ValueError(f"bad period label: {label!r}")
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    return label, date(year, eom_month, eom_day)


# ---------------------------------------------------------------------------
# Number / range parsing
# ---------------------------------------------------------------------------

def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _parse_value(s: str) -> tuple[float | None, float | None]:
    """Parse '35.90' -> (35.90, None);  '34.6-35.8' -> (34.6, 35.8);
    '63.0%-65.0%' -> (63.0, 65.0);  '31.6' -> (31.6, None)."""
    s = (s or "").strip().replace("%", "").replace(",", "")
    if not s:
        return None, None
    # Range with hyphen, en-dash or em-dash (TSMC uses hyphen-minus).
    rng = re.match(r"\s*(-?\d+\.?\d*)\s*[-–—]\s*(-?\d+\.?\d*)\s*$", s)
    if rng:
        return float(rng.group(1)), float(rng.group(2))
    try:
        return float(s), None
    except ValueError:
        return None, None


# ---------------------------------------------------------------------------
# Row spec — maps Chinese row label → (metric, unit)
# ---------------------------------------------------------------------------

ROW_LABEL_MAP: list[tuple[str, str, str]] = [
    # (substring in the row label, metric_name, unit)
    ("營業收入淨額",   "revenue",            "usd_b"),
    ("平均匯率",       "usd_ntd_avg_rate",   "ntd_per_usd"),
    ("營業毛利率",     "gross_margin",       "pct"),
    ("營業淨利率",     "operating_margin",   "pct"),
]


# ---------------------------------------------------------------------------
# Fact model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuidanceFact:
    ticker: str
    period_end: date          # the period being guided FOR
    period_label: str
    metric: str
    bound: str                # "actual" / "low" / "high" / "point"
    value: float
    unit: str
    guidance_issued_at: date  # the page's quarter (when the guidance was published)
    source: str
    extracted_at: str


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

_GUIDANCE_TABLE_RE = re.compile(
    r"<table\b[^>]*>(.*?)</table>", re.IGNORECASE | re.DOTALL,
)


def _find_guidance_table(html: str) -> str | None:
    """Return the inner HTML of the page's <table> that contains 業績展望.
    The page typically has exactly one <table> on the quarterly-results page."""
    for m in _GUIDANCE_TABLE_RE.finditer(html):
        if "業績展望" in m.group(1):
            return m.group(1)
    return None


def _parse_table_rows(table_html: str) -> list[list[str]]:
    """Return list of rows, each a list of cell strings (<td> or <th>)."""
    rows = []
    for tr_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
        tr_inner = tr_match.group(1)
        cells = []
        for c in re.finditer(r"<(t[dh])\b[^>]*>(.*?)</\1>", tr_inner, re.IGNORECASE | re.DOTALL):
            cells.append(_strip_html(c.group(2)))
        if cells:
            rows.append(cells)
    return rows


def _detect_period_columns(rows: list[list[str]]) -> tuple[str, str] | None:
    """Find the two period labels in the header rows. Looks for cells that
    match `\d?Q?\d{1,2}` shape — typically '1Q26' and '2Q26'."""
    candidates: list[str] = []
    for row in rows[:3]:    # only header rows
        for cell in row:
            cell = cell.strip()
            if re.match(r"^\d?Q\d{2}$", cell) or re.match(r"^Q\d\s*\d{2,4}$", cell):
                candidates.append(cell)
    # Take the first 2 distinct period labels found
    out: list[str] = []
    for c in candidates:
        if c not in out:
            out.append(c)
        if len(out) == 2:
            break
    if len(out) == 2:
        return out[0], out[1]
    return None


def extract_guidance_from_html(
    html: str,
    *,
    ticker: str,
    page_period_label: str,    # e.g. "1Q26" — taken from the URL path
    source_url: str | None = None,
) -> tuple[dict, list[GuidanceFact]]:
    """Parse the guidance table from a saved /chinese/quarterly-results/{Y}/q{N}
    page HTML. Returns (bronze, list[GuidanceFact])."""
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _, page_pe = parse_period_label(page_period_label)

    table_html = _find_guidance_table(html)
    if table_html is None:
        raise RuntimeError("No <table> with 業績展望 marker found on page")

    rows = _parse_table_rows(table_html)
    if not rows:
        raise RuntimeError("Guidance table has no <tr> rows")

    periods = _detect_period_columns(rows)
    if periods is None:
        raise RuntimeError(
            f"Could not detect 2 period labels in header. First 3 rows: {rows[:3]!r}"
        )
    current_period_label, next_period_label = periods
    _, current_pe = parse_period_label(current_period_label)
    _, next_pe = parse_period_label(next_period_label)

    facts: list[GuidanceFact] = []
    source_id = f"tsmc_quarterly_results_page_{page_period_label}"

    for row in rows:
        if not row:
            continue
        label = row[0]
        # Match by Chinese substring
        spec = next(
            ((m, u) for sub, m, u in ROW_LABEL_MAP if sub in label),
            None,
        )
        if spec is None:
            continue
        metric, unit = spec
        # Expect 3 value columns: actual, current-period guidance, next-period guidance
        cells = row[1:]
        if len(cells) < 3:
            continue
        actual_low, actual_high = _parse_value(cells[0])
        cur_low, cur_high = _parse_value(cells[1])
        next_low, next_high = _parse_value(cells[2])

        # Column 1: actuals for the current page period
        if actual_low is not None and actual_high is None:
            facts.append(GuidanceFact(
                ticker=ticker, period_end=current_pe, period_label=current_period_label,
                metric=metric, bound="actual", value=actual_low, unit=unit,
                guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
            ))

        # Column 2: original guidance for the SAME (just-ended) quarter
        if cur_low is not None:
            if cur_high is None:
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=current_pe, period_label=current_period_label,
                    metric=metric, bound="point", value=cur_low, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))
            else:
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=current_pe, period_label=current_period_label,
                    metric=metric, bound="low", value=cur_low, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=current_pe, period_label=current_period_label,
                    metric=metric, bound="high", value=cur_high, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))

        # Column 3: fresh guidance for the NEXT quarter
        if next_low is not None:
            if next_high is None:
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=next_pe, period_label=next_period_label,
                    metric=metric, bound="point", value=next_low, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))
            else:
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=next_pe, period_label=next_period_label,
                    metric=metric, bound="low", value=next_low, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))
                facts.append(GuidanceFact(
                    ticker=ticker, period_end=next_pe, period_label=next_period_label,
                    metric=metric, bound="high", value=next_high, unit=unit,
                    guidance_issued_at=page_pe, source=source_id, extracted_at=extracted_at,
                ))

    bronze = {
        "ticker": ticker,
        "page_period_label": page_period_label,
        "page_period_end": page_pe.isoformat(),
        "source_id": source_id,
        "source_url": source_url,
        "extracted_at": extracted_at,
        "table_periods": [current_period_label, next_period_label],
        "table_rows_raw": rows,
        "fact_count": len(facts),
    }
    return bronze, facts


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, page_period_label: str) -> Path:
    _, pe = parse_period_label(page_period_label)
    q = page_period_label[0]
    return BRONZE_ROOT / ticker / str(pe.year) / f"Q{q}" / "guidance_page.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["page_period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(facts: list[GuidanceFact], ticker: str) -> Path:
    if not facts:
        raise RuntimeError("upsert_silver called with no facts")
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    out = SILVER_ROOT / f"{ticker}.parquet"
    new_df = pd.DataFrame([asdict(f) for f in facts])
    if out.exists():
        existing = pd.read_parquet(out)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["ticker", "period_end", "metric", "bound", "guidance_issued_at", "source"],
            keep="last",
        )
    else:
        combined = new_df
    combined = combined.sort_values(
        ["period_end", "metric", "bound", "guidance_issued_at"],
        ascending=[False, True, True, False],
    )
    combined.to_parquet(out, index=False, compression="zstd")
    return out
