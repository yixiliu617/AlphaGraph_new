"""
TSMC quarterly Management Report extractor.

Layered output:
- BRONZE: raw extracted JSON per report (page text + verbatim parsed tables).
- SILVER: long-format Parquet of facts. One row per (ticker, period_end,
  metric, dimension, source).

Bronze:  backend/data/financials/raw/{ticker}/{year}/{Q}/management_report.json
Silver:  backend/data/financials/quarterly_facts/{ticker}.parquet

Each TSMC report shows the latest quarter PLUS the prior quarter (QoQ
reference) and the year-ago quarter (YoY reference). We emit facts for
ALL 3 periods, tagged with `source = 'tsmc_management_report_<label>'`
of the report that produced the row. Future reports overlap on prior
periods — that's a feature: comparing rows for the same period from
different sources surfaces restatements and label changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "quarterly_facts"


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"\b(\d)Q(\d{2})\b")


def parse_period_label(label: str) -> tuple[str, date]:
    """'1Q26' -> ('1Q26', date(2026, 3, 31))."""
    m = _PERIOD_RE.match(label)
    if not m:
        raise ValueError(f"Unrecognised period label: {label!r}")
    q = int(m.group(1))
    yy = int(m.group(2))
    # 2-digit year heuristic: <50 -> 2000s, else 1900s. TSMC reports go back to 1997.
    year = 2000 + yy if yy < 50 else 1900 + yy
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    return label, date(year, eom_month, eom_day)


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^[\s\(]*-?[\d,]+\.?\d*[\s\)%]*$")
# A standalone numeric token; matches "1,134.10", "(382.80)", "66.2%", "-1.13"
_NUM_TOKEN_RE = re.compile(r"\(?-?[\d,]+\.?\d*\)?%?")


def parse_num(s: str) -> float | None:
    """'1,134.10' / '(382.80)' / '66.2%' / '-' -> 1134.10 / -382.80 / 66.2 / None."""
    s = s.strip()
    if not s or s in {"-", "—", "N/A", "n/a"}:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fact model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    ticker: str
    period_end: date
    period_label: str
    metric: str
    dimension: str   # "" for headline (no breakdown)
    value: float
    unit: str
    source: str
    extracted_at: str


# ---------------------------------------------------------------------------
# Generic label-then-N-values table parser
# ---------------------------------------------------------------------------

def _find_section_lines(text: str, anchor: str, end_anchors: tuple[str, ...] = ()) -> list[str] | None:
    """Slice the text starting at the line containing `anchor`, ending just
    before the first line containing any of `end_anchors`. Returns None if
    `anchor` not found."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if anchor in ln), None)
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if any(e in lines[j] for e in end_anchors):
            end = j
            break
    return lines[start:end]


def _detect_periods(section_lines: list[str], max_scan: int = 25) -> list[str]:
    """Find the first run of >=2 consecutive period labels in the slice."""
    labels: list[str] = []
    started = False
    for line in section_lines[:max_scan]:
        s = line.strip()
        if not s:
            if started:
                continue
            else:
                continue
        if _PERIOD_RE.match(s):
            labels.append(s)
            started = True
        elif started:
            break
    return labels


def _parse_value_table(
    section_lines: list[str],
    row_specs: list[tuple[str, str, str]],
    period_labels: list[str] | None = None,
) -> Iterator[tuple[str, str, float, str]]:
    """Yields (metric, period_label, value, unit) for each labelled row.

    Each row in `row_specs` is (label_regex, metric_name, unit). The parser
    finds the label, then takes the next len(period_labels) numeric values
    (one-per-line OR multiple-per-line) and pairs them with the periods.
    """
    if period_labels is None:
        period_labels = _detect_periods(section_lines)
        if not period_labels:
            return
    for label_pat, metric, unit in row_specs:
        rgx = re.compile(label_pat, re.MULTILINE)
        idx = None
        for i, line in enumerate(section_lines):
            if rgx.search(line):
                idx = i
                break
        if idx is None:
            continue
        values, _ = _take_n_numbers(section_lines, idx + 1, len(period_labels))
        for plabel, val in zip(period_labels, values):
            if val is not None:
                yield metric, plabel, val, unit


# ---------------------------------------------------------------------------
# Page 1 Summary table parser
# ---------------------------------------------------------------------------
#
# The Summary table on page 1 has a fixed set of rows. PyMuPDF emits each
# label and each number on its own line (with stray whitespace lines mixed
# in). We anchor on the period header (e.g. "1Q26\n4Q25\n1Q25\nQoQ\nYoY"),
# then for each known label-prefix we walk forward and collect the next 3
# numeric tokens — those are the 3 period values. QoQ/YoY % we ignore here
# (they're derivable from the period values; storing them duplicates info).

# Order matters: longer / more-specific labels first so they don't get
# shadowed by shorter prefixes ("Net Revenue (US$ billions)" before "Net Revenue").
SUMMARY_ROWS: list[tuple[str, str, str]] = [
    # (label_pattern, metric_name, unit)
    (r"EPS \(NT\$ per common share\)",          "eps",                  "ntd_per_share"),
    (r"\(US\$ per ADR unit\)",                  "eps_adr",              "usd_per_adr"),
    (r"Net Revenue \(US\$ billions\)",          "net_revenue_usd",      "usd_b"),
    (r"^Net Revenue\s*$",                       "net_revenue",          "ntd_b"),
    (r"^Gross Profit\s*$",                      "gross_profit",         "ntd_b"),
    (r"^Gross Margin\s*$",                      "gross_margin",         "pct"),
    (r"^Operating Expenses\s*$",                "operating_expenses",   "ntd_b"),
    (r"^Other Operating Income and Expenses",   "other_operating_income","ntd_b"),
    (r"^Operating Income\s*$",                  "operating_income",     "ntd_b"),
    (r"^Operating Margin\s*$",                  "operating_margin",     "pct"),
    (r"^Non-Operating Items\s*$",               "non_operating_items",  "ntd_b"),
    (r"^Net Income Attributable",               "net_income",           "ntd_b"),
    (r"^Net Profit Margin\s*$",                 "net_profit_margin",    "pct"),
    (r"^Wafer Shipment",                        "wafer_shipment",       "kpcs_12in_eq"),
    (r"^Average Exchange Rate",                 "usd_ntd_avg_rate",     "ntd_per_usd"),
]


def _find_period_header(lines: list[str]) -> tuple[int, list[str]] | None:
    """Find an index i such that lines[i:i+3] are 3 period labels (e.g. 1Q26, 4Q25, 1Q25).
    Returns (i, [period_labels]) or None."""
    for i in range(len(lines) - 2):
        a, b, c = lines[i].strip(), lines[i + 1].strip(), lines[i + 2].strip()
        if _PERIOD_RE.match(a) and _PERIOD_RE.match(b) and _PERIOD_RE.match(c):
            return i, [a, b, c]
    return None


def _take_n_numbers(lines: list[str], start: int, n: int) -> tuple[list[float | None], int]:
    """From `start`, walk forward and return the next `n` numeric values.

    Handles BOTH layout variants emitted by PyMuPDF on these reports:
      - one number per line (most rows on page 1)
      - multiple numbers on one line (e.g. " 1,134.10  1,046.09  839.25"
        for the NT$-billion rows, where Workiva puts them tighter)

    Stops at the first line that is neither blank nor purely numeric.
    Returns (values, index_after_last_consumed_line).
    """
    out: list[float | None] = []
    i = start
    started = False
    skipped_continuations = 0
    MAX_LABEL_CONTINUATIONS = 2   # e.g. "Net Income Attributable to Shareholders of the / Parent Company"
    while i < len(lines) and len(out) < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        chunks = s.split()
        if all(_NUM_TOKEN_RE.fullmatch(c) for c in chunks):
            for c in chunks:
                if len(out) >= n:
                    break
                out.append(parse_num(c))
            started = True
            i += 1
            continue
        if not started and skipped_continuations < MAX_LABEL_CONTINUATIONS:
            # Treat as label-continuation (multi-line row label); keep walking.
            skipped_continuations += 1
            i += 1
            continue
        break
    return out, i


def parse_summary_table(text: str, period_labels: list[str]) -> Iterator[tuple[str, str, float, str]]:
    """Yields (metric, period_label, value, unit) tuples from the page-1 Summary table."""
    yield from _parse_value_table(text.splitlines(), SUMMARY_ROWS, period_labels)


# ---------------------------------------------------------------------------
# Generic "Net Revenue by X" / "Wafer Revenue by X" parser
# ---------------------------------------------------------------------------
#
# Each breakdown table on Page 2 follows the layout:
#     <Section Header>
#     1Q26
#     4Q25
#     1Q25
#     <segment label 1>
#     <pct1>
#     <pct2>
#     <pct3>
#     <segment label 2>
#     ...
# Stops when a new "by X" header appears or text becomes narrative.

_SEGMENT_NUM_RE = re.compile(r"^\s*\d{1,3}%\s*$")  # e.g. "25%", "3%"

_SEGMENT_TABLES = [
    # (section_header_substring, metric_name, end_marker_substrings)
    ("Wafer Revenue by Technology",
     "revenue_share_by_technology",
     ("Net Revenue by Platform", "Net Revenue by Geography", "TSMC")),
    ("Net Revenue by Platform",
     "revenue_share_by_platform",
     ("Net Revenue by Geography", "TSMC")),
    ("Net Revenue by Geography",
     "revenue_share_by_geography",
     ("TSMC", "I.  Revenue Analysis")),
]


def parse_segment_tables(text: str, period_labels: list[str]) -> Iterator[tuple[str, str, str, float]]:
    """Yields (metric, segment_label, period_label, value) for each row of each
    Wafer/Net Revenue by X table. Values are percentages (already divided to 0-100, not 0-1)."""
    lines = text.splitlines()
    for header_sub, metric, end_subs in _SEGMENT_TABLES:
        # Find the header line.
        start = None
        for i, line in enumerate(lines):
            if header_sub in line:
                start = i
                break
        if start is None:
            continue
        # Skip the period header row (3 period labels) right after.
        i = start + 1
        # Walk past blank lines + the 3 period labels
        period_seen = 0
        while i < len(lines) and period_seen < len(period_labels):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if _PERIOD_RE.match(s):
                period_seen += 1
                i += 1
                continue
            break  # something else
        # Now read (segment_label, val, val, val) repeats until end marker or
        # a non-percentage where we expected percentages.
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if any(end in s for end in end_subs):
                break
            # Treat this line as a segment label.
            seg_label = s
            i += 1
            vals, i = _take_n_numbers(lines, i, len(period_labels))
            if not vals:
                break  # malformed; bail
            for plabel, v in zip(period_labels, vals):
                if v is not None:
                    yield metric, seg_label, plabel, v


# ---------------------------------------------------------------------------
# Sub-table specs for pages 2-5
# ---------------------------------------------------------------------------
# Each entry: (anchor, end_anchors, row_specs)
# - anchor: substring that uniquely identifies the section header
# - end_anchors: substrings that mark the next section (search bounded)
# - row_specs: list of (label_regex, metric_name, unit)
#
# Rows that duplicate Page-1 Summary metrics are intentionally OMITTED here
# (e.g. the II-2 row "Total Operating Expenses" maps to summary's
# `operating_expenses`; including it again would emit redundant rows). For
# every duplicated metric we keep the Summary as canonical.

# II-1 Gross Profit Analysis (page 2): only Cost of Revenue is unique here.
GROSS_PROFIT_ROWS: list[tuple[str, str, str]] = [
    (r"^Cost of Revenue\b", "cost_of_revenue", "ntd_b"),
]

# II-2 Operating Income Analysis (page 3): R&D + SG&A + ratio.
OPERATING_INCOME_ROWS: list[tuple[str, str, str]] = [
    (r"^Research & Development\s*$",                        "r_and_d",                         "ntd_b"),
    (r"^SG&A\s*$",                                          "sga",                             "ntd_b"),
    (r"^Total Operating Expenses as % ?$",                  "opex_pct_of_revenue",             "pct"),
]

# II-3 Non-Operating Items (page 3): decomposition of non_operating_items.
NON_OPERATING_ROWS: list[tuple[str, str, str]] = [
    (r"^L-T Investments\s*$",                               "non_op_lt_investments",           "ntd_b"),
    (r"^Net Interest Income \(Expenses\)",                  "non_op_net_interest_income",      "ntd_b"),
    (r"^Other Gains and Losses\s*$",                        "non_op_other_gains_losses",       "ntd_b"),
]

# II-4 Net Profit and EPS (page 3): tax detail.
NET_PROFIT_ROWS: list[tuple[str, str, str]] = [
    (r"^Income before Tax\s*$",                             "income_before_tax",               "ntd_b"),
    (r"^Income Tax Expenses\s*$",                           "income_tax_expenses",             "ntd_b"),
    (r"^Effective Tax Rate\s*$",                            "effective_tax_rate",              "pct"),
]

# III-1 Liquidity Analysis / Balance Sheet (page 4).
BALANCE_SHEET_ROWS: list[tuple[str, str, str]] = [
    (r"^Cash & Marketable Securities\s*$",                  "cash_and_marketable_securities",  "ntd_b"),
    (r"^Accounts Receivable\s*$",                           "accounts_receivable",             "ntd_b"),
    (r"^Inventories\s*$",                                   "inventories",                     "ntd_b"),
    (r"^Other Current Assets\s*$",                          "other_current_assets",            "ntd_b"),
    (r"^Total Current Assets\s*$",                          "total_current_assets",            "ntd_b"),
    (r"^Accounts Payable\s*$",                              "accounts_payable",                "ntd_b"),
    (r"^Current Portion of Bonds Payable",                  "current_portion_bonds_loans",     "ntd_b"),
    (r"^Dividends Payable\s*$",                             "dividends_payable",               "ntd_b"),
    (r"^Accrued Liabilities and Others\s*$",                "accrued_liabilities_and_others",  "ntd_b"),
    (r"^Total Current Liabilities\s*$",                     "total_current_liabilities",       "ntd_b"),
    (r"^Current Ratio \(x\)",                               "current_ratio",                   "ratio"),
    (r"^Net Working Capital\s*$",                           "net_working_capital",             "ntd_b"),
]

# III-2 Receivable / Inventory Days (page 4).
DAYS_ROWS: list[tuple[str, str, str]] = [
    (r"^Days of Receivable\s*$",                            "days_of_receivable",              "days"),
    (r"^Days of Inventory\s*$",                             "days_of_inventory",               "days"),
]

# III-3 Debt Service (page 4): only items unique to this section.
DEBT_SERVICE_ROWS: list[tuple[str, str, str]] = [
    (r"^Interest-Bearing Debts\s*$",                        "interest_bearing_debts",          "ntd_b"),
    (r"^Net Cash Reserves\s*$",                             "net_cash_reserves",               "ntd_b"),
]

# IV-1 Quarterly Cash Flow Analysis (page 5).
CASH_FLOW_ROWS: list[tuple[str, str, str]] = [
    (r"^Depreciation & Amortization\s*$",                   "depreciation_and_amortization",   "ntd_b"),
    (r"^Other Operating Sources/\(Uses\)\s*$",              "cf_other_operating",              "ntd_b"),
    (r"^Net Operating Sources/\(Uses\)\s*$",                "cf_operating",                    "ntd_b"),
    (r"^Capital Expenditures\s*$",                          "capex",                           "ntd_b"),
    (r"^Marketable Financial Instruments\s*$",              "cf_marketable_financial_instruments", "ntd_b"),
    (r"^Other Investing Sources/\(Uses\)\s*$",              "cf_other_investing",              "ntd_b"),
    (r"^Net Investing Sources/\(Uses\)\s*$",                "cf_investing",                    "ntd_b"),
    (r"^Cash Dividends\s*$",                                "cash_dividends",                  "ntd_b"),
    (r"^Bonds Payable\s*$",                                 "cf_bonds_payable",                "ntd_b"),
    (r"^Other Financing Sources/\(Uses\)\s*$",              "cf_other_financing",              "ntd_b"),
    (r"^Net Financing Sources/\(Uses\)\s*$",                "cf_financing",                    "ntd_b"),
    (r"^Exchange Rate Changes\s*$",                         "cf_exchange_rate_changes",        "ntd_b"),
    (r"^Cash Position Net Changes\s*$",                     "cash_position_net_changes",       "ntd_b"),
    (r"^Ending Cash Balance\s*$",                           "ending_cash_balance",             "ntd_b"),
]

# IV-2 Free Cash Flow (page 5): DIFFERENT period scheme — current + 3 prior
# quarters in chronological progression (1Q26 / 4Q25 / 3Q25 / 2Q25), not
# the report's standard YoY+QoQ. Detect periods locally inside the section.
FREE_CASH_FLOW_ROWS: list[tuple[str, str, str]] = [
    (r"^Free Cash Flow\s*$",                                "free_cash_flow",                  "ntd_b"),
]

# V. CapEx (page 5): USD billions; only 2 periods (current Q + prior Q).
CAPEX_USD_ROWS: list[tuple[str, str, str]] = [
    (r"^Capital Expenditures\s*$",                          "capex_usd",                       "usd_b"),
]


def parse_pages_2_to_5(
    pages_text: list[tuple[int, str]],
    period_labels: list[str],
) -> Iterator[tuple[str, str, float, str]]:
    """Run all sub-table parsers across pages 2-5 and yield headline facts.

    `period_labels` is the report-level header (3 periods). For tables that
    use a different period scheme (Free Cash Flow, CapEx USD), we detect
    periods locally inside the section.
    """
    pages = {p: t for p, t in pages_text}

    # Page 2: II-1 Gross Profit Analysis (Cost of Revenue only)
    if 2 in pages:
        sec = _find_section_lines(pages[2], "II - 1. Gross Profit Analysis", ("II - 2.", "Page 3"))
        if sec is None:
            sec = _find_section_lines(pages[2], "Cost of Revenue", ("Page 3",))
        if sec:
            yield from _parse_value_table(sec, GROSS_PROFIT_ROWS, period_labels)

    # Page 3: II-2 / II-3 / II-4
    if 3 in pages:
        for anchor, end_anchors, rows in [
            ("II - 2. Operating Income Analysis", ("II - 3.",),                OPERATING_INCOME_ROWS),
            ("II - 3. Non-Operating Items",        ("II - 4.",),                NON_OPERATING_ROWS),
            ("II - 4. Net Profit and EPS",         ("Page 4", "III. Financial"), NET_PROFIT_ROWS),
        ]:
            sec = _find_section_lines(pages[3], anchor, end_anchors)
            if sec:
                yield from _parse_value_table(sec, rows, period_labels)

    # Page 4: III-1 / III-2 / III-3
    if 4 in pages:
        for anchor, end_anchors, rows in [
            ("III - 1. Liquidity Analysis", ("III - 2.",),                BALANCE_SHEET_ROWS),
            ("III - 2. Receivable/Inventory Days", ("III - 3.",),         DAYS_ROWS),
            ("III - 3. Debt Service", ("Page 5", "IV. Cash Flow"),        DEBT_SERVICE_ROWS),
        ]:
            sec = _find_section_lines(pages[4], anchor, end_anchors)
            if sec:
                yield from _parse_value_table(sec, rows, period_labels)

    # Page 5: IV-1 / IV-2 / V.
    if 5 in pages:
        # IV-1 uses report-level periods.
        sec = _find_section_lines(pages[5], "IV - 1. Quarterly Cash Flow Analysis",
                                  ("IV-2", "IV - 2", "V. CapEx", "V. Capital"))
        if sec:
            yield from _parse_value_table(sec, CASH_FLOW_ROWS, period_labels)

        # IV-2 has its own quarterly progression (4 periods).
        sec = _find_section_lines(pages[5], "IV-2 Free Cash Flow",
                                  ("V. CapEx", "V. Capital"))
        if sec is None:
            sec = _find_section_lines(pages[5], "IV - 2", ("V. CapEx", "V. Capital"))
        if sec:
            local_periods = _detect_periods(sec)
            if local_periods:
                yield from _parse_value_table(sec, FREE_CASH_FLOW_ROWS, local_periods)

        # V. CapEx (USD): 2 periods only.
        sec = _find_section_lines(pages[5], "V. Capital Expenditures",
                                  ("VI. Recap",))
        if sec:
            local_periods = _detect_periods(sec)
            if local_periods:
                yield from _parse_value_table(sec, CAPEX_USD_ROWS, local_periods)


# ---------------------------------------------------------------------------
# Top-level extractor
# ---------------------------------------------------------------------------

def extract_pdf(
    pdf_path: Path,
    *,
    ticker: str,
    report_period_label: str,
    source_url: str | None = None,
) -> tuple[dict, list[Fact]]:
    """Extract bronze (dict) + silver (list[Fact]) from one Management Report PDF."""
    pdf_bytes = Path(pdf_path).read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    doc.close()

    # ------- Find the period labels actually printed on Page 1 -------
    page1 = pages_text[0][1] if pages_text else ""
    page2 = pages_text[1][1] if len(pages_text) > 1 else ""
    hdr = _find_period_header(page1.splitlines())
    if hdr is None:
        raise RuntimeError("Could not locate period header on page 1 — PDF layout changed?")
    _, period_labels = hdr

    # ------- Sanity-check that the report's headline period matches the arg ------
    if report_period_label and period_labels[0] != report_period_label:
        raise RuntimeError(
            f"Report period mismatch: caller said {report_period_label!r}, "
            f"page 1 shows {period_labels[0]!r}"
        )

    source_id = f"tsmc_management_report_{period_labels[0]}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------- Build silver facts -------
    facts: list[Fact] = []

    def emit(metric: str, dimension: str, plabel: str, value: float, unit: str) -> None:
        _, pe = parse_period_label(plabel)
        facts.append(Fact(
            ticker=ticker,
            period_end=pe,
            period_label=plabel,
            metric=metric,
            dimension=dimension,
            value=value,
            unit=unit,
            source=source_id,
            extracted_at=extracted_at,
        ))

    # Page 1 — Summary table
    for metric, plabel, value, unit in parse_summary_table(page1, period_labels):
        emit(metric, "", plabel, value, unit)
    # Page 2 — Revenue breakdowns (segmented)
    for metric, seg, plabel, value in parse_segment_tables(page2, period_labels):
        emit(metric, seg, plabel, value, "pct")
    # Pages 2-5 — sub-tables (Cost of Revenue, R&D/SG&A, tax, balance sheet,
    # cash flow, capex). All headline (no dimension).
    for metric, plabel, value, unit in parse_pages_2_to_5(pages_text, period_labels):
        emit(metric, "", plabel, value, unit)

    # ------- Build bronze JSON -------
    bronze = {
        "ticker": ticker,
        "report_period_label": period_labels[0],
        "report_period_end": parse_period_label(period_labels[0])[1].isoformat(),
        "periods_in_report": period_labels,
        "source_id": source_id,
        "source_url": source_url,
        "source_pdf_sha256": sha,
        "source_pdf_bytes": len(pdf_bytes),
        "extracted_at": extracted_at,
        "pages": [{"page": p, "text": t} for p, t in pages_text],
    }
    return bronze, facts


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, period_label: str) -> Path:
    label, _ = parse_period_label(period_label)
    q = label[:2]                      # "1Q"
    year = parse_period_label(label)[1].year
    return BRONZE_ROOT / ticker / str(year) / q / "management_report.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["report_period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(facts: list[Fact], ticker: str) -> Path:
    """Append + dedup on (ticker, period_end, metric, dimension, source)."""
    if not facts:
        raise RuntimeError("upsert_silver called with no facts")
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    out = SILVER_ROOT / f"{ticker}.parquet"
    new_df = pd.DataFrame([asdict(f) for f in facts])
    if out.exists():
        existing = pd.read_parquet(out)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["ticker", "period_end", "metric", "dimension", "source"],
            keep="last",
        )
    else:
        combined = new_df
    combined = combined.sort_values(["period_end", "metric", "dimension"], ascending=[False, True, True])
    combined.to_parquet(out, index=False, compression="zstd")
    return out
