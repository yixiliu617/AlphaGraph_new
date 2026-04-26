"""
UMC (2303.TW) quarterly Management Report extractor.

UMC's site is structurally simpler than TSMC's:
  - No Cloudflare; PDFs fetchable via plain HTTPS GET.
  - Per-quarter detail page: /en/Download/quarterly_results/QuarterlyResultsDetail/{YYYY}/{YYYY}Q{N}.
  - 4 PDF types per quarter (when fully published):
      UMC{YY}Q{N}_report.pdf                  -> Quarterly Report (this extractor)
      UMC{YY}Q{N}_financial_presentation-E.pdf -> Investor presentation
      UMC{YY}Q{N}_financial_statements-E.pdf   -> Audited financial statements
      UMC{YY}Q{N}_conference_call.pdf          -> Earnings release / call summary

Layout family: Microsoft Word (legacy), single-column with summary
tables on pages 3-5 ("Operating Results", "Operating Expenses", "Non-
Operating Income", "Cash Flow"). Sums + ratios laid out the same way
TSMC's pre-Workiva era did, so we share `_quarterly_common.py` with
TSMC and only define UMC-specific row specs and section anchors.

UMC reports headline amounts in **NT$ million** (TSMC uses NT$ billion).
The unit string (`ntd_m`) flows through the silver schema so a downstream
join across foundries can normalise to a common scale.

Output layers (mirrors TSMC):
  BRONZE: backend/data/financials/raw/2303.TW/{YYYY}/Q{N}/management_report.json
  SILVER: backend/data/financials/quarterly_facts/2303.TW.parquet
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import fitz
import pandas as pd

from backend.scripts.extractors._quarterly_common import (
    parse_value_table, detect_periods, find_section_lines,
    take_n_numbers, DEFAULT_PERIOD_RE,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "quarterly_facts"

TICKER = "2303.TW"


_FY_RE = re.compile(r"^FY(\d{2})$")


def parse_period_label(label: str) -> tuple[str, date]:
    """'3Q25' -> ('3Q25', 2025-09-30); 'FY25' -> ('FY25', 2025-12-31)."""
    fy = _FY_RE.match(label)
    if fy:
        yy = int(fy.group(1))
        year = 2000 + yy if yy < 50 else 1900 + yy
        return label, date(year, 12, 31)
    m = DEFAULT_PERIOD_RE.match(label)
    if not m:
        raise ValueError(f"bad period label: {label!r}")
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    return label, date(year, eom_month, eom_day)


# ---------------------------------------------------------------------------
# UMC-specific section specs
# ---------------------------------------------------------------------------
#
# UMC's "Operating Results" table on page 3 has columns:
#   {curQ}  {prevQ}  QoQ %  {YoYQ}  YoY %
# (5 cells per row vs TSMC's 5 in same shape). Currency is NT$ million.

# UMC's Operating Results table (page 3) has 5 columns:
#   {curQ} {prevQ} QoQ%   {YoYQ} YoY%
# Period values live at row indices [0, 1, 3] — column 2 is QoQ %, column
# 4 is YoY %, both derivable from the period values so we drop them.
OPERATING_RESULTS_ROWS: list[tuple[str, str, str]] = [
    (r"^Operating Revenues\s*$",                            "net_revenue",            "ntd_m"),
    (r"^Gross Profit\s*$",                                  "gross_profit",           "ntd_m"),
    (r"^Operating Expenses\s*$",                            "operating_expenses",     "ntd_m"),
    (r"^Net Other Operating Income and Expenses\s*$",       "other_operating_income", "ntd_m"),
    (r"^Operating Income\s*$",                              "operating_income",       "ntd_m"),
    (r"^Net Non-Operating Income and Expenses\s*$",         "non_operating_items",    "ntd_m"),
    (r"^Net Income Attributable",                           "net_income",             "ntd_m"),
    (r"^EPS\s*\(NT\$",                                      "eps",                    "ntd_per_share"),
    (r"^EPS\s*\(US\$",                                      "eps_adr",                "usd_per_adr"),
    (r"^Exchange rate \(USD/NTD\)",                         "usd_ntd_avg_rate",       "ntd_per_usd"),
]

# UMC's revenue breakdown tables: similar to TSMC but the rows differ —
# UMC reports by Geography, by Technology Node, and by IDM/Communication/
# Computer/Consumer end-market (NOT TSMC's Platform taxonomy).

# UMC publishes 4 segment-share breakdowns on pages 4-5, each as a 5-period
# rolling view (current Q + prior 4 Qs). Layout:
#   <Section header>
#   <Column-name line>          e.g. "Region" / "Geometry" / "Customer Type" / "Application"
#   <Period header line>        e.g. "3Q25 2Q25 1Q25 4Q24 3Q24" (all 5 on ONE line)
#   <segment_label>             e.g. "North America"
#   <5 percent values>          one per line
#   ... repeat per segment ...
# Each entry: (anchor_substring, metric_name, end_anchor_substrings)
SEGMENT_TABLES = [
    ("Revenue Breakdown by Region",
     "revenue_share_by_geography",
     ("Revenue Breakdown by", "Wafer Shipments", "Wafer shipments")),
    ("Revenue Breakdown by Geometry",
     "revenue_share_by_technology",
     ("Revenue Breakdown by", "Wafer Shipments", "Wafer shipments")),
    ("Revenue Breakdown by Customer Type",
     "revenue_share_by_customer_type",
     ("Revenue Breakdown by", "Wafer Shipments", "Wafer shipments")),
    ("Revenue Breakdown by Application",
     "revenue_share_by_application",
     ("(1) Computer", "Wafer Shipments", "Wafer shipments")),
]

# Column-name lines that immediately precede the period-header row in each
# of UMC's segment tables. These are skipped (not treated as segment labels)
# during the walk.
_SEGMENT_COL_NAMES = {"Region", "Geometry", "Customer Type", "Application"}


# ---------------------------------------------------------------------------
# Capacity / Wafer Shipments / Utilization
# ---------------------------------------------------------------------------
# UMC's mgmt report has 3 small tables on page 8-9, each 5-period rolling:
#   Wafer Shipments        (12" K equivalents)
#   Quarterly Capacity Utilization Rate
#   Total Capacity         (12" K equivalents)
# Layout per block:
#   <Section header>          e.g. "Wafer Shipments"
#   <Period header line>      e.g. "3Q25 2Q25 1Q25 4Q24 3Q24"
#   <metric label line>       e.g. "Wafer Shipments" / "Utilization Rate" / "Total Capacity"
#   [optional unit line]      e.g. "(12" K equivalents)"
#   <5 numeric values, one per line>
#
# (anchor, metric, unit, end_anchors)
CAPACITY_TABLES = [
    ("Wafer Shipments",
     "wafer_shipments",   "kpcs_12in_eq",
     ("Quarterly Capacity Utilization", "Total Capacity", "Capacity4")),
    ("Quarterly Capacity Utilization Rate",
     "capacity_utilization", "pct",
     ("Total Capacity", "Capacity4")),
    ("Total Capacity",
     "total_capacity",    "kpcs_12in_eq",
     ("Capacity4", "Annual Capacity in", "Quarterly Capacity in")),
]

# Lines that follow the section header but aren't the metric value row —
# they're the metric's repeated label or unit annotation.
_CAPACITY_LABEL_LINES = {
    "Wafer Shipments",
    "Utilization Rate",
    "Total Capacity",
    '(12" K equivalents)',
    '(12” K equivalents)',  # smart-quote variant
    '(8" K equivalents)',
    '(8” K equivalents)',
}


# ---------------------------------------------------------------------------
# Cash Flow Summary (page 5) — 2 periods (curQ, prevQ), NT$ million
# ---------------------------------------------------------------------------
# Section anchor "Cash Flow Summary". Layout: each row label on its own line,
# followed by curQ value, then prevQ value (one per line). Sub-rows are
# indented one column. Negative cash flows shown as (X).

CASH_FLOW_ROWS: list[tuple[str, str, str]] = [
    (r"^Cash Flow from Operating Activities\s*$",  "cash_flow_from_operating",      "ntd_m"),
    (r"^Net income before tax\s*$",                "net_income_before_tax",         "ntd_m"),
    (r"^Depreciation\s*&\s*Amortization\s*$",      "depreciation_amortization",     "ntd_m"),
    (r"^Income tax paid\s*$",                      "income_tax_paid",               "ntd_m"),
    (r"^Cash Flow from Investing Activities\s*$",  "cash_flow_from_investing",      "ntd_m"),
    (r"^Acquisition of PP&E\s*$",                  "capex_ppe",                     "ntd_m"),
    (r"^Acquisition of intangible assets\s*$",     "capex_intangibles",             "ntd_m"),
    (r"^Cash Flow from Financing Activities\s*$",  "cash_flow_from_financing",      "ntd_m"),
    (r"^Bank loans\s*$",                           "bank_loans_change",             "ntd_m"),
    (r"^Bonds issued\s*$",                         "bonds_issued",                  "ntd_m"),
    (r"^Cash dividends\s*$",                       "cash_dividends_paid",           "ntd_m"),
    (r"^Effect of Exchange Rate\s*$",              "fx_effect_on_cash",             "ntd_m"),
    (r"^Net Cash Flow\s*$",                        "net_cash_flow",                 "ntd_m"),
    (r"^Beginning balance\s*$",                    "cash_beginning_balance",        "ntd_m"),
    (r"^Ending balance\s*$",                       "cash_ending_balance",           "ntd_m"),
]


# ---------------------------------------------------------------------------
# Balance Sheet Highlights (page 6) — 3 periods, NT$ BILLION
# ---------------------------------------------------------------------------
# Two sub-tables sharing the same period header (curQ / prevQ / YoY-Q):
#   "Current Assets" -> cash, AR, DSO, inventory, DOI, total CA
#   "Liabilities"    -> total CL, AP, ST debt, equipment payables, other,
#                       LT debt, total liabilities, debt-to-equity
# Period values one per line; sub-rows indented (leading spaces).

BALANCE_SHEET_ROWS: list[tuple[str, str, str]] = [
    # Current Assets section
    (r"^Cash and Cash Equivalents\s*$",        "cash_and_equivalents",       "ntd_b"),
    (r"^Accounts Receivable\s*$",              "accounts_receivable",        "ntd_b"),
    (r"^\s*Days Sales Outstanding\s*$",        "days_sales_outstanding",     "days"),
    (r"^Inventories,\s*net\s*$",               "inventories_net",            "ntd_b"),
    (r"^\s*Days of Inventory\s*$",             "days_of_inventory",          "days"),
    (r"^Total Current Assets\s*$",             "total_current_assets",       "ntd_b"),
    # Liabilities section
    (r"^Total Current Liabilities\s*$",        "total_current_liabilities",  "ntd_b"),
    (r"^\s*Accounts Payable\s*$",              "accounts_payable",           "ntd_b"),
    (r"^\s*Short-Term Credit / Bonds\s*$",     "short_term_debt",            "ntd_b"),
    (r"^\s*Payables on Equipment\s*$",         "equipment_payables",         "ntd_b"),
    (r"^Long-Term Credit / Bonds\s*$",         "long_term_debt",             "ntd_b"),
    (r"^Total Liabilities\s*$",                "total_liabilities",          "ntd_b"),
    (r"^Debt to Equity\s*$",                   "debt_to_equity",             "pct"),
]


# ---------------------------------------------------------------------------
# Annual / Full-Year Results (page 10) — 2 periods (FYxx, FYxx-1), NT$ million
# ---------------------------------------------------------------------------
# Period header on this page is just two years like "2025 2024" — NOT the
# usual {N}Q{YY} form. We build period_labels manually as ['FY25', 'FY24'].

ANNUAL_RESULTS_ROWS: list[tuple[str, str, str]] = [
    (r"^Operating Revenues\s*$",                            "net_revenue",            "ntd_m"),
    (r"^Gross Profit\s*$",                                  "gross_profit",           "ntd_m"),
    (r"^Operating Expenses\s*$",                            "operating_expenses",     "ntd_m"),
    (r"^Net Other Operating Income",                        "other_operating_income", "ntd_m"),
    (r"^Operating Income\s*$",                              "operating_income",       "ntd_m"),
    (r"^Net Non-Operating Income",                          "non_operating_items",    "ntd_m"),
    (r"^Income Tax Expense\s*$",                            "income_tax_expense",     "ntd_m"),
    (r"^Net Income Attributable",                           "net_income",             "ntd_m"),
    (r"^EPS\s*\(NT\$",                                      "eps",                    "ntd_per_share"),
    (r"^EPS\s*\(US\$",                                      "eps_adr",                "usd_per_adr"),
    (r"^Exchange rate \(USD/NTD\)",                         "usd_ntd_avg_rate",       "ntd_per_usd"),
]


def _match_period(periods: list[str], plabel: str) -> str | None:
    """Returns the period label matching by Q+year, since UMC may write
    '3Q25' inconsistently (with/without space). Currently a no-op; here
    for future-proofing if we encounter normalised variants."""
    for p in periods:
        if p == plabel:
            return p
    return None


@dataclass(frozen=True)
class Fact:
    ticker: str
    period_end: date
    period_label: str
    metric: str
    dimension: str
    value: float
    unit: str
    source: str
    extracted_at: str


def extract_pdf(
    pdf_path: Path,
    *,
    ticker: str = TICKER,
    report_period_label: str,
    source_url: str | None = None,
) -> tuple[dict, list[Fact], list[dict]]:
    """Extract bronze JSON + silver Fact list + guidance records from one UMC quarterly report PDF.

    Returns (bronze_dict, facts, guidance_records). Guidance records are
    dicts with keys: ticker, issued_in_period_label, for_period_label,
    metric, bound, value, unit, text, source, extracted_at.
    """
    pdf_bytes = Path(pdf_path).read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    doc.close()
    full_text = "\n".join(t for _, t in pages_text)

    # --- Find the Operating Results table; it's the canonical anchor. -----
    # The "Operating Results" label appears on page 3 right above the data
    # table. End anchors guard against running past it into the next
    # section ("Operating Expenses", "Cash Flow", etc.).
    sec = find_section_lines(
        full_text, "Operating Results\n",
        end_anchors=("Note：Sums may not", "Note: Sums may not", "Operating Expenses\n",
                     "Cash Flow", "Revenue Breakdown", "\nUMC "),
    )
    # Fallback: if the precise header anchor missed (whitespace variance),
    # try the looser "Operating Results" alone.
    if sec is None:
        sec = find_section_lines(full_text, "Operating Results")
    if sec is None:
        raise RuntimeError("Could not find 'Operating Results' section in PDF")

    # UMC's column header is 3 periods INTERLEAVED with QoQ%/YoY% labels:
    #   {curQ} / {prevQ} / "QoQ % change" / {YoYQ} / "YoY % change"
    # Old reports (pre-2023) wrap their prose paragraphs per-word, leaving
    # stray standalone period tokens (e.g. "1Q22") in the commentary that
    # would fullmatch. Require the 3 period labels to cluster within a
    # 10-line window — the real table header always does; stray prose
    # tokens are 5+ lines apart.
    period_labels: list[str] = []
    fullmatch_idx = [
        i for i, line in enumerate(sec[:300])
        if DEFAULT_PERIOD_RE.fullmatch(line.strip())
    ]
    # Pick the first window of 3 fullmatch lines that (a) spans ≤ 8 lines
    # and (b) has 3 DISTINCT period labels. Stray prose periods repeat curQ
    # and so fail the distinctness check.
    for start in range(len(fullmatch_idx) - 2):
        window = fullmatch_idx[start:start + 3]
        labels = [sec[i].strip() for i in window]
        if window[2] - window[0] <= 8 and len(set(labels)) == 3:
            period_labels = labels
            break
    if not period_labels:
        raise RuntimeError(f"No period header detected. First 10 lines: {sec[:10]!r}")
    if report_period_label and period_labels[0] != report_period_label:
        print(f"  [warn] period header {period_labels[0]} != arg {report_period_label}")

    source_id = f"umc_management_report_{period_labels[0]}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

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

    # Walk Operating Results rows. Each row has 5 numeric values:
    #   index 0 = current Q  (period_labels[0])
    #   index 1 = prev Q     (period_labels[1])
    #   index 2 = QoQ %      (skip — derivable)
    #   index 3 = YoY Q      (period_labels[2])
    #   index 4 = YoY %      (skip — derivable)
    # Some rows (EPS / FX) have only 4 values (no QoQ %); pair them
    # positionally by length.
    for label_pat, metric, unit in OPERATING_RESULTS_ROWS:
        rgx = re.compile(label_pat, re.MULTILINE)
        idx = next((i for i, line in enumerate(sec) if rgx.search(line)), None)
        if idx is None:
            continue
        vals, _ = take_n_numbers(sec, idx + 1, 5)
        if not vals:
            continue
        # Period values live at indices 0, 1, 3 in the 5-column layout. If
        # we got fewer than 4 values total (EPS-style row with no %s),
        # treat each value as positionally aligned to a period label.
        if len(vals) >= 4:
            mapping = [(0, period_labels[0]), (1, period_labels[1]), (3, period_labels[2])]
        else:
            mapping = [(i, period_labels[i]) for i in range(min(len(vals), len(period_labels)))]
        for col_idx, plabel in mapping:
            if col_idx < len(vals) and vals[col_idx] is not None:
                emit(metric, "", plabel, vals[col_idx], unit)

    # Segment revenue breakdowns (4 tables, 5 periods each, percent values)
    for anchor, metric, end_anchors in SEGMENT_TABLES:
        seg = find_section_lines(full_text, anchor, end_anchors)
        if seg is None:
            continue
        # The period header is variable-shape across UMC report eras:
        #   2022+ : single line, e.g. "3Q25 2Q25 1Q25 4Q24 3Q24"
        #   pre-2022: split across 3-5 lines, e.g.
        #             "1Q20" / "4Q19 3Q19" / "2Q19" / "1Q19"
        # Strategy: find the first line where every space-separated token
        # is a period-fullmatch, then keep collecting tokens from immediately
        # following lines that are also all-periods. Stop at the first
        # non-period line — that's the first segment label.
        seg_periods: list[str] = []
        first_idx = -1
        last_idx = -1
        for i, line in enumerate(seg[:10]):
            tokens = line.strip().split()
            if tokens and all(DEFAULT_PERIOD_RE.fullmatch(t) for t in tokens):
                if first_idx == -1:
                    first_idx = i
                seg_periods.extend(tokens)
                last_idx = i
            elif seg_periods:
                break
        if len(seg_periods) < 2:
            continue
        # Walk segment rows below the (possibly multi-line) period header.
        i = last_idx + 1
        while i < len(seg):
            label = seg[i].strip()
            if not label:
                i += 1
                continue
            # Skip column-name rows ("Region"/"Geometry"/etc.) and end markers.
            if label in _SEGMENT_COL_NAMES or any(e in label for e in end_anchors):
                i += 1
                continue
            i += 1
            vals, i = take_n_numbers(seg, i, len(seg_periods))
            if not vals:
                continue
            for plabel, v in zip(seg_periods, vals):
                if v is not None and 0 <= v <= 100:
                    emit(metric, label, plabel, v, "pct")

    # Capacity / Wafer Shipments / Utilization (3 small 5-period tables).
    # Total Capacity reuses the period header from the table above it
    # (Utilization), so we compute a rolling-5 fallback from the report's
    # primary period.
    def _rolling5(cur: str) -> list[str]:
        m = DEFAULT_PERIOD_RE.match(cur)
        q = int(m.group(1))
        yy = int(m.group(2))
        year = 2000 + yy if yy < 50 else 1900 + yy
        out = []
        for offset in range(0, 5):
            t = year * 4 + (q - 1) - offset
            ny, nq = divmod(t, 4)
            out.append(f"{nq + 1}Q{str(ny)[2:]}")
        return out

    fallback_periods = _rolling5(period_labels[0]) if period_labels else []

    # Detect the wafer-equivalent unit used in this report. UMC switched
    # from 8" K equivalents (pre-2024) to 12" K equivalents (2024+).
    # Storing the unit explicitly avoids cross-era contamination when a
    # downstream consumer averages overlapping periods.
    if '12" K equivalents' in full_text or '12” K equivalents' in full_text:
        wafer_unit = "kpcs_12in_eq"
    elif '8" K equivalents' in full_text or '8” K equivalents' in full_text:
        wafer_unit = "kpcs_8in_eq"
    else:
        wafer_unit = "kpcs_unknown"

    for anchor, metric, unit, end_anchors in CAPACITY_TABLES:
        # Override 'kpcs_12in_eq' default with the report-detected unit.
        if unit == "kpcs_12in_eq":
            unit = wafer_unit
        cap = find_section_lines(full_text, anchor, end_anchors)
        if cap is None:
            continue
        cap_periods: list[str] = []
        first_idx = -1
        last_idx = -1
        for i, line in enumerate(cap[:10]):
            s = line.strip()
            if not s:
                continue
            tokens = s.split()
            if all(DEFAULT_PERIOD_RE.fullmatch(t) for t in tokens):
                if first_idx == -1:
                    first_idx = i
                cap_periods.extend(tokens)
                last_idx = i
            elif cap_periods:
                break
        if len(cap_periods) < 2:
            # Total Capacity has no period header in its own slice — it
            # reuses the header above it. Fall back to rolling-5 from cur.
            if not fallback_periods:
                continue
            cap_periods = fallback_periods[:]
            last_idx = 0  # start scanning right after the section header
        # Skip past metric-label / unit-annotation lines after the period
        # header, then take exactly len(cap_periods) numeric values.
        i = last_idx + 1
        while i < len(cap):
            s = cap[i].strip()
            if not s or s in _CAPACITY_LABEL_LINES:
                i += 1
                continue
            break
        vals, _ = take_n_numbers(cap, i, len(cap_periods))
        for plabel, v in zip(cap_periods, vals):
            if v is None:
                continue
            if metric == "capacity_utilization" and not (0 <= v <= 100):
                continue
            emit(metric, "", plabel, v, unit)

    # ---------------------------------------------------------------------
    # Cash Flow Summary (page 5) — 2 periods, 1 value per row per period
    # ---------------------------------------------------------------------
    cf_sec = find_section_lines(
        full_text, "Cash Flow Summary",
        end_anchors=("Note：Sums may not", "Note: Sums may not",
                     "Cash and cash equivalents", "Current Assets"),
    )
    cf_periods: list[str] = []
    if cf_sec:
        # Period header lines look like "For the 3-Month / Period Ended /
        # Dec. 31, 2025" then "For the 3-Month / Period Ended / Sep. 30, 2025"
        # We extract by month-end date and convert to {N}Q{YY} form.
        date_rgx = re.compile(r"(Mar|Jun|Sep|Dec)\.\s*(\d{1,2}),\s*(\d{4})")
        for line in cf_sec[:30]:
            m = date_rgx.search(line)
            if m:
                mon = {"Mar": 1, "Jun": 2, "Sep": 3, "Dec": 4}[m.group(1)]
                yr = int(m.group(3))
                cf_periods.append(f"{mon}Q{str(yr)[2:]}")
            if len(cf_periods) >= 2:
                break
    if len(cf_periods) >= 2 and cf_sec:
        for label_pat, metric, unit in CASH_FLOW_ROWS:
            rgx = re.compile(label_pat, re.MULTILINE)
            idx = next((i for i, line in enumerate(cf_sec) if rgx.search(line)), None)
            if idx is None:
                continue
            vals, _ = take_n_numbers(cf_sec, idx + 1, 2)
            for plabel, v in zip(cf_periods, vals):
                if v is not None:
                    emit(metric, "", plabel, v, unit)

        # Derived: capex_total = capex_ppe + capex_intangibles (all positive
        # magnitudes since stored values are negative cash outflows). Free
        # cash flow = cash_flow_from_operating + capex_ppe + capex_intangibles
        # (capex values are negative so addition yields ops-minus-capex).
        emitted_lookup: dict[tuple[str, str], float] = {}
        for f in facts:
            if f.source == source_id:
                emitted_lookup[(f.metric, f.period_label)] = f.value
        for plabel in cf_periods:
            ppe = emitted_lookup.get(("capex_ppe", plabel))
            intang = emitted_lookup.get(("capex_intangibles", plabel))
            ops = emitted_lookup.get(("cash_flow_from_operating", plabel))
            if ppe is not None and intang is not None:
                emit("capex_total", "", plabel, abs(ppe) + abs(intang), "ntd_m")
            if ops is not None and ppe is not None:
                fcf = ops + ppe + (intang or 0)
                emit("free_cash_flow", "", plabel, fcf, "ntd_m")

    # ---------------------------------------------------------------------
    # Balance Sheet Highlights (page 6) — 3 periods, NT$ billion
    # ---------------------------------------------------------------------
    bs_sec = find_section_lines(
        full_text, "Current Assets",
        end_anchors=("Analysis of Revenue", "Revenue Breakdown",
                     "Blended ASP", "Wafer Shipments"),
    )
    bs_periods: list[str] = []
    if bs_sec:
        # Period header line: "4Q25 3Q25 4Q24" (single-line space-separated)
        # OR multi-line variant.
        for i, line in enumerate(bs_sec[:15]):
            s = line.strip()
            if not s:
                continue
            tokens = s.split()
            if all(DEFAULT_PERIOD_RE.fullmatch(t) for t in tokens):
                bs_periods.extend(tokens)
                if len(bs_periods) >= 3:
                    break
            elif bs_periods:
                break
    if len(bs_periods) >= 2 and bs_sec:
        bs_periods = bs_periods[:3]
        for label_pat, metric, unit in BALANCE_SHEET_ROWS:
            rgx = re.compile(label_pat, re.MULTILINE)
            idx = next((i for i, line in enumerate(bs_sec) if rgx.search(line)), None)
            if idx is None:
                continue
            vals, _ = take_n_numbers(bs_sec, idx + 1, len(bs_periods))
            for plabel, v in zip(bs_periods, vals):
                if v is not None:
                    emit(metric, "", plabel, v, unit)

    # ---------------------------------------------------------------------
    # Annual / Full-Year Results (page 10) — 2 periods FYxx, FYxx-1
    # Only present in Q4 reports where the full year wraps up.
    # ---------------------------------------------------------------------
    fy_sec = find_section_lines(
        full_text, "Brief Summary of Full Year",
        end_anchors=("First Quarter", "First quarter",
                     "Outlook", "Recent Developments", "Conference Call"),
    )
    if fy_sec:
        # Period header: two consecutive year tokens like "2025" then "2024"
        # (each on its own line) OR "2025 2024" on one line. The header sits
        # below a multi-page-of-blank-lines whitespace block (PowerPoint
        # chart leaks through fitz as ~25 blank lines), so scan the entire
        # slice rather than just the prologue.
        year_re = re.compile(r"^(20\d{2})$")
        # Find the first line where two CONSECUTIVE non-blank lines are both
        # year tokens — that's the table header. Skips the bullet summary
        # at the top which mentions years inline ("2024" inside prose).
        fy_periods_raw: list[int] = []
        for i, line in enumerate(fy_sec):
            tokens = line.strip().split()
            if len(tokens) == 1 and year_re.match(tokens[0]):
                # Look-ahead: is the NEXT non-blank line also a year token?
                for j in range(i + 1, min(i + 4, len(fy_sec))):
                    nxt = fy_sec[j].strip()
                    if not nxt:
                        continue
                    nxt_toks = nxt.split()
                    if len(nxt_toks) == 1 and year_re.match(nxt_toks[0]):
                        fy_periods_raw = [int(tokens[0]), int(nxt_toks[0])]
                    break
                if fy_periods_raw:
                    break
            elif len(tokens) >= 2 and all(year_re.match(t) for t in tokens[:2]):
                # Single-line variant: "2025 2024"
                fy_periods_raw = [int(tokens[0]), int(tokens[1])]
                break
        fy_periods = [f"FY{str(y)[2:]}" for y in fy_periods_raw[:2]]

        if len(fy_periods) >= 2:
            for label_pat, metric, unit in ANNUAL_RESULTS_ROWS:
                rgx = re.compile(label_pat, re.MULTILINE)
                idx = next((i for i, line in enumerate(fy_sec) if rgx.search(line)), None)
                if idx is None:
                    continue
                vals, _ = take_n_numbers(fy_sec, idx + 1, 3)  # FY1, FY2, YoY%
                if not vals:
                    continue
                # Layout: FY current, FY prior, YoY% — pair first 2 to periods.
                for plabel, v in zip(fy_periods, vals[:2]):
                    if v is not None:
                        emit(metric, f"annual:{plabel}", plabel, v, unit)

    # ---------------------------------------------------------------------
    # Forward Guidance (page 11) — qualitative bullets for next quarter
    # Stored under metric prefix "guidance_" with dimension="for:{next_q}",
    # bound="verbal" or "low"/"high"/"point" when an implied range exists.
    # ---------------------------------------------------------------------
    guidance_facts: list[dict] = []
    if period_labels:
        cur_period_for_guidance = period_labels[0]
        # Compute next quarter from cur_period
        m_cur = DEFAULT_PERIOD_RE.match(cur_period_for_guidance)
        cq = int(m_cur.group(1))
        cyy = int(m_cur.group(2))
        cyear = 2000 + cyy if cyy < 50 else 1900 + cyy
        nq_total = cyear * 4 + cq
        nyear, nq_idx = divmod(nq_total, 4)
        next_q_label = f"{nq_idx + 1}Q{str(nyear)[2:]}"
        guidance_for_period = next_q_label

        guide_sec = find_section_lines(
            full_text, "Outlook",
            end_anchors=("Recent Developments", "Conference Call",
                         "Conference Call /", "Safe Harbor"),
        )
        if guide_sec is None:
            guide_sec = find_section_lines(
                full_text, "Quarter-over-Quarter Guidance",
                end_anchors=("Recent Developments", "Conference Call",
                             "Safe Harbor"),
            )
        if guide_sec:
            # Each bullet looks like:
            #   "Wafer Shipments: Will remain flat"
            #   "ASP in USD: Will remain firm"
            #   "Gross Profit Margin: Will be approximately in the high-20% range"
            #   "Capacity Utilization: mid-70% range"
            #   "2026 CAPEX: US$1.5 billion"
            joined = "\n".join(guide_sec)
            # Bullet labels may start with a digit (e.g. "2026 CAPEX") so we
            # accept any non-colon character class for the first char.
            bullet_rgx = re.compile(
                r"(?:●|⚫|\*|-|·)\s*([\w\d][^:\n]{1,60}?):\s*([^\n●⚫]{1,200})",
                re.IGNORECASE,
            )
            METRIC_MAP = {
                "wafer shipments":      ("guidance_wafer_shipments_qoq", None),
                "asp in usd":           ("guidance_asp_usd_qoq",         None),
                "gross profit margin":  ("guidance_gross_margin",        "pct"),
                "operating margin":     ("guidance_operating_margin",    "pct"),
                "capacity utilization": ("guidance_capacity_utilization","pct"),
                "capex":                ("guidance_annual_capex",        "usd_b"),
            }
            # Range parsing helpers
            range_rgx       = re.compile(r"(?:approximately\s+)?(?:in\s+the\s+)?(low|mid|high)-(\d+)%(?:\s+range)?", re.IGNORECASE)
            simple_pct_rgx  = re.compile(r"([\d.]+)%\s*(?:to|–|-)\s*([\d.]+)%", re.IGNORECASE)
            usd_b_rgx       = re.compile(r"US\$\s*([\d.]+)\s*billion", re.IGNORECASE)

            def _emit_guide(metric: str, bound: str, value: float, unit: str, text: str) -> None:
                guidance_facts.append({
                    "ticker": ticker,
                    "issued_in_period_label": cur_period_for_guidance,
                    "for_period_label": guidance_for_period,
                    "metric": metric,
                    "bound": bound,
                    "value": value,
                    "unit": unit,
                    "text": text,
                    "source": source_id,
                    "extracted_at": extracted_at,
                })

            for bm in bullet_rgx.finditer(joined):
                label = bm.group(1).strip()
                value_text = bm.group(2).strip()
                lower = label.lower()
                # Capture year prefix if present (e.g. "2026 CAPEX") — annual
                # guidance items belong to a full fiscal year, not a quarter.
                year_prefix = re.match(r"^(\d{4})\s+", lower)
                stripped = re.sub(r"^\d{4}\s+", "", lower).strip()
                key = next((k for k in METRIC_MAP if k in stripped), None)
                if not key:
                    continue
                metric_name, default_unit = METRIC_MAP[key]
                # Override for_period for annual items (CAPEX): use FY{yy}
                local_for_period = guidance_for_period
                if year_prefix and "capex" in stripped:
                    yy = year_prefix.group(1)[2:]
                    local_for_period = f"FY{yy}"
                # Inline closure: re-bind _emit_guide's for_period locally.
                def _emit_local(metric: str, bound: str, value: float, unit: str, text: str) -> None:
                    guidance_facts.append({
                        "ticker": ticker,
                        "issued_in_period_label": cur_period_for_guidance,
                        "for_period_label": local_for_period,
                        "metric": metric,
                        "bound": bound,
                        "value": value,
                        "unit": unit,
                        "text": text,
                        "source": source_id,
                        "extracted_at": extracted_at,
                    })
                # Always store the verbal text record
                _emit_local(metric_name, "verbal", None, default_unit or "text",
                            value_text)
                # Try to extract structured numeric range
                rm = range_rgx.search(value_text)
                if rm:
                    qual = rm.group(1).lower()
                    base = int(rm.group(2))
                    # Heuristic: 'low-X%' = X-1 to X+1  (we'll use X as midpoint, base+0/+3 for high-/low-)
                    if qual == "low":
                        lo, hi = base, base + 3
                    elif qual == "mid":
                        lo, hi = base + 3, base + 7
                    else:  # high
                        lo, hi = base + 6, base + 9
                    _emit_local(metric_name, "low",  float(lo), default_unit or "pct", value_text)
                    _emit_local(metric_name, "high", float(hi), default_unit or "pct", value_text)
                    _emit_local(metric_name, "midpoint", (lo + hi) / 2.0, default_unit or "pct", value_text)
                    continue
                spm = simple_pct_rgx.search(value_text)
                if spm:
                    lo, hi = float(spm.group(1)), float(spm.group(2))
                    _emit_local(metric_name, "low",  lo, default_unit or "pct", value_text)
                    _emit_local(metric_name, "high", hi, default_unit or "pct", value_text)
                    _emit_local(metric_name, "midpoint", (lo + hi) / 2.0, default_unit or "pct", value_text)
                    continue
                um = usd_b_rgx.search(value_text)
                if um:
                    point = float(um.group(1))
                    _emit_local(metric_name, "point", point, "usd_b", value_text)

    bronze = {
        "ticker": ticker,
        "report_period_label": period_labels[0] if period_labels else report_period_label,
        "report_period_end": parse_period_label(period_labels[0])[1].isoformat() if period_labels else None,
        "periods_in_report": period_labels,
        "source_id": source_id,
        "source_url": source_url,
        "source_pdf_sha256": sha,
        "source_pdf_bytes": len(pdf_bytes),
        "extracted_at": extracted_at,
        "guidance_count": len(guidance_facts),
        "pages": [{"page": p, "text": t} for p, t in pages_text],
    }
    return bronze, facts, guidance_facts


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, period_label: str) -> Path:
    m = DEFAULT_PERIOD_RE.match(period_label)
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    return BRONZE_ROOT / ticker / str(year) / f"Q{q}" / "management_report.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["report_period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(facts: list[Fact], ticker: str = TICKER) -> Path:
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
    combined = combined.sort_values(
        ["period_end", "metric", "dimension"],
        ascending=[False, True, True],
    )
    combined.to_parquet(out, index=False, compression="zstd")
    return out


# ---------------------------------------------------------------------------
# Guidance silver layer — separate parquet so the guidance-vs-actual join
# can pivot independently (mirrors TSMC's design).
# ---------------------------------------------------------------------------

GUIDANCE_ROOT = DATA_ROOT / "guidance"


def upsert_guidance(records: list[dict], ticker: str = TICKER) -> Path | None:
    """Append + dedup guidance records on (ticker, issued_in_period_label,
    for_period_label, metric, bound, source). Returns None if no records."""
    if not records:
        return None
    GUIDANCE_ROOT.mkdir(parents=True, exist_ok=True)
    out = GUIDANCE_ROOT / f"{ticker}.parquet"
    new_df = pd.DataFrame(records)
    if out.exists():
        existing = pd.read_parquet(out)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["ticker", "issued_in_period_label", "for_period_label",
                    "metric", "bound", "source"],
            keep="last",
        )
    else:
        combined = new_df
    combined = combined.sort_values(
        ["issued_in_period_label", "metric", "bound"], ascending=[False, True, True],
    )
    combined.to_parquet(out, index=False, compression="zstd")
    return out
