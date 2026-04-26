"""
MediaTek (2454.TW) quarterly Press Release extractor.

MediaTek's IR site is structurally simpler than TSMC's and as simple as
UMC's:
  - No Cloudflare; PDFs fetchable via plain HTTPS GET.
  - HubSpot-hosted single-page index at
    /investor-relations/financial-information lists every PDF anchor for the
    full history (Press Release back to 2012, Financial Reports back to
    2003) — no quarter dropdown / SPA.
  - Per quarter: Press Release / Presentation / Transcript / Financial
    Statements / Earnings call invitation.

Layout family: HYBRID — narrative-prose pages 1-3, then a clean
Consolidated Income Statement table on pages ~4-5.

The narrative on pages 1-3 ("Operating expenses for the quarter were
NT$X million ... NT$Y in the previous quarter and NT$Z in the year-ago
quarter") is what most people see, but it's harder to parse robustly. The
Consolidated Income Statement table embedded later in the same PDF gives
us a clean 3-period × 14-row P&L grid:

    (In NT$ millions, except EPS)   4Q25  3Q25  4Q24  Q-Q  Y-Y
    Net Sales                       150,188  142,097  138,043  5.7%  8.8%
    Operating costs                 (80,907)  (75,985)  (71,042)
    Gross profit                    69,281  66,112  67,001  4.8%  3.4%
      Selling expenses              (5,529)  (4,984)  (5,642)
      Administration expenses       (2,653)  (2,588)  (3,194)
      R&D expenses                  (39,248)  (36,353)  (36,752)
    Operating expenses              (47,431)  (43,924)  (45,589)
    Operating income                21,850  22,188  21,412  (1.5%)  2.0%
    Net non-operating income        5,298  7,772  4,799
    Net income before income tax    27,147  29,960  26,211
      Income tax expense            (4,074)  (4,509)  (2,270)
    Net income                      23,074  25,451  23,941  (9.3%)  (3.6%)
      Owners of the parent          22,925  25,221  23,789
      Non-controlling interests     148  230  152
    EPS attributable to the parent  14.39  15.84  14.95

Each value is on its own line in the PyMuPDF text extraction, so we reuse
`_quarterly_common.take_n_numbers` exactly the way UMC's report uses it.

A small set of metrics aren't in the income statement table (gross margin
%, operating margin %, net profit margin %, operating cash flow, FX rate)
— those are pulled from the narrative prose on pages 1-3.

Output layers (mirrors TSMC + UMC):
  BRONZE: backend/data/financials/raw/2454.TW/{YYYY}/Q{N}/press_release.json
  SILVER: backend/data/financials/quarterly_facts/2454.TW.parquet
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import fitz
import pandas as pd

from backend.scripts.extractors._quarterly_common import (
    DEFAULT_PERIOD_RE,
    find_section_lines,
    take_n_numbers,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "quarterly_facts"

TICKER = "2454.TW"

_FULL_PERIOD_RE = re.compile(r"^(\d)Q(\d{2})$")


def parse_period_label(label: str) -> tuple[str, date]:
    m = _FULL_PERIOD_RE.match(label)
    if not m:
        raise ValueError(f"bad period label: {label!r}")
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    return label, date(year, eom_month, eom_day)


# ---------------------------------------------------------------------------
# Income-statement table specs
# ---------------------------------------------------------------------------
# Each row: (label_regex, metric_name, unit, sign).
#   sign = +1 for positive contributions, -1 for "expenses" rows where the
#   prose puts negatives in parens but we want the absolute value (so
#   downstream consumers can sum costs without bookkeeping). Income/profit
#   rows pass through with their natural sign.
#
# parens_to_neg already happens in `parse_num`. This `sign` flag flips the
# sign back to positive for cost rows so e.g. r_and_d, operating_expenses,
# cost_of_revenue all carry positive magnitudes — same convention as UMC's
# operating_expenses fact (which UMC reports as negative; we kept that as-
# is for UMC, but MediaTek's prose breakdown reads more naturally with
# positive expense magnitudes).
INCOME_STATEMENT_ROWS: list[tuple[str, str, str, int]] = [
    (r"^\s*Net Sales\s*$",                       "net_revenue",            "ntd_m", +1),
    (r"^\s*Operating costs\s*$",                 "cost_of_revenue",        "ntd_m", -1),
    (r"^\s*Gross profit\s*$",                    "gross_profit",           "ntd_m", +1),
    (r"^\s*Selling expenses\s*$",                "selling_expenses",       "ntd_m", -1),
    (r"^\s*Administration expenses\s*$",         "g_and_a",                "ntd_m", -1),
    (r"^\s*R&D expenses\s*$",                    "r_and_d",                "ntd_m", -1),
    (r"^\s*Operating expenses\s*$",              "operating_expenses",     "ntd_m", -1),
    (r"^\s*Operating income\s*$",                "operating_income",       "ntd_m", +1),
    (r"^\s*Net non-operating income\b",          "non_operating_items",    "ntd_m", +1),
    (r"^\s*Net income before income tax\s*$",    "net_income_before_tax",  "ntd_m", +1),
    (r"^\s*Income tax expense\s*$",              "income_tax_expense",     "ntd_m", -1),
    (r"^\s*Net income\s*$",                      "net_income",             "ntd_m", +1),
    (r"^\s*Owners of the parent\s*$",            "net_income_attributable","ntd_m", +1),
    (r"^\s*Non-controlling interests\s*$",       "minority_interests",     "ntd_m", +1),
    # EPS row is wrapped: "EPS attributable to the\nparent(NT$)\n14.39"
    (r"^EPS attributable to the\s*$",            "eps",                    "ntd_per_share", +1),
]


# Numeric tokens for prose-extraction patterns (margins / cash flow / FX)
_NUM_RE = r"([\d,]+(?:\.\d+)?)"


# ---------------------------------------------------------------------------
# Period inference
# ---------------------------------------------------------------------------

def _infer_report_period(text: str) -> str | None:
    """The page header at the top of every page is just the period label
    on its own line ('4Q25')."""
    for line in text[:600].splitlines():
        s = line.strip()
        if _FULL_PERIOD_RE.fullmatch(s):
            return s
    m = re.search(
        r"Reports\s+(First|Second|Third|Fourth)-Quarter\s+(?:and\s+Full-Year\s+)?(\d{4})",
        text,
    )
    if m:
        q = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}[m.group(1)]
        return f"{q}Q{str(int(m.group(2)))[2:]}"
    return None


# ---------------------------------------------------------------------------
# Top-level extractor
# ---------------------------------------------------------------------------

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
    report_period_label: str | None = None,
    source_url: str | None = None,
) -> tuple[dict, list[Fact]]:
    """Extract bronze JSON + silver Fact list from one MediaTek press release."""
    pdf_bytes = Path(pdf_path).read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    doc.close()
    full_text = "\n".join(t for _, t in pages_text)

    detected = _infer_report_period(full_text)
    cur_period = report_period_label or detected
    if not cur_period:
        raise RuntimeError("Could not infer report period from PDF text")
    if report_period_label and detected and report_period_label != detected:
        print(f"  [warn] period header {detected!r} != arg {report_period_label!r}")

    source_id = f"mediatek_press_release_{cur_period}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    facts: list[Fact] = []
    seen: set[tuple[str, str, str]] = set()

    def emit(metric: str, dimension: str, plabel: str, value: float, unit: str) -> None:
        key = (metric, dimension, plabel)
        if key in seen:
            return
        seen.add(key)
        _, pe = parse_period_label(plabel)
        facts.append(Fact(
            ticker=ticker, period_end=pe, period_label=plabel,
            metric=metric, dimension=dimension, value=value, unit=unit,
            source=source_id, extracted_at=extracted_at,
        ))

    # ---- (1) Income statement table --------------------------------------
    sec = find_section_lines(
        full_text,
        "Consolidated Income Statement",
        end_anchors=("Supplemental Information",
                     "Reconciliations of TIFRS",
                     "Reconciliation of TIFRS"),
    )
    period_labels: list[str] = []
    if sec:
        # The period header is split across multiple lines (each label on its
        # own line, with blank lines between). Same shape as UMC pre-2022
        # segment tables: collect consecutive period-fullmatch lines into a
        # cluster, stop at first non-period line.
        first_idx = -1
        last_idx = -1
        # Some 2019-era reports decorate prev/YoY period labels with
        # '(Note2)' suffix to flag a restatement footnote — strip those
        # before testing fullmatch and before recording the canonical
        # period label.
        def _norm(tok: str) -> str:
            return re.sub(r"\([^)]*\)", "", tok).strip()

        for i, line in enumerate(sec[:15]):
            s = line.strip()
            if not s:
                # Blank lines separate the period tokens in MediaTek's
                # PyMuPDF text — keep walking, don't terminate the cluster.
                continue
            tokens = s.split()
            normed = [_norm(t) for t in tokens]
            if all(DEFAULT_PERIOD_RE.fullmatch(t) for t in normed):
                if first_idx == -1:
                    first_idx = i
                period_labels.extend(normed)
                last_idx = i
            elif period_labels:
                break
        if len(period_labels) >= 3:
            period_labels = period_labels[:3]
            for label_pat, metric, unit, sign in INCOME_STATEMENT_ROWS:
                rgx = re.compile(label_pat)
                idx = next((i for i, line in enumerate(sec) if rgx.search(line)), None)
                if idx is None:
                    continue
                vals, _ = take_n_numbers(sec, idx + 1, 3)
                for plabel, v in zip(period_labels, vals):
                    if v is None:
                        continue
                    emit(metric, "", plabel, abs(v) if sign == -1 else v, unit)

    # If the income-statement table failed, fall back to inferred 3-period
    # labels for the prose-only extracts below.
    if not period_labels:
        from datetime import date as _date
        m = _FULL_PERIOD_RE.match(cur_period)
        q = int(m.group(1)); yy = int(m.group(2))
        year = 2000 + yy if yy < 50 else 1900 + yy
        prev = (year * 4 + q - 2)
        yoy = (year * 4 + q - 5)
        period_labels = [
            cur_period,
            f"{(prev % 4) + 1}Q{str(prev // 4)[2:]}",
            f"{(yoy % 4) + 1}Q{str(yoy // 4)[2:]}",
        ]

    # ---- (2) Margin ratios — prose only, three-period form ---------------
    # "{Metric} for the quarter was X%, [...] from Y% in the previous quarter
    #  and [...] Z% in the year-ago quarter"
    margin_phrases = [
        (r"Gross\s+margin",         "gross_margin",       "pct"),
        (r"Operating\s+margin",     "operating_margin",   "pct"),
        (r"Net\s+profit\s+margin",  "net_profit_margin",  "pct"),
    ]
    for phrase, metric, unit in margin_phrases:
        rgx = re.compile(
            rf"{phrase}\s+for\s+the\s+quarter\s+was\s+{_NUM_RE}%"
            rf"[\s\S]{{0,180}}?{_NUM_RE}%\s+in\s+the\s+previous\s+quarter"
            rf"[\s\S]{{0,180}}?{_NUM_RE}%\s+in\s+the\s+year-ago\s+quarter",
            re.IGNORECASE,
        )
        m = rgx.search(full_text)
        if not m:
            continue
        for plabel, v in zip(period_labels, m.groups()):
            try:
                fv = float(v)
            except ValueError:
                continue
            emit(metric, "", plabel, fv, unit)

    # ---- (3) Operating cash flow (prose) ---------------------------------
    cf_rgx = re.compile(
        rf"Net\s+cash\s+provided\s+by\s+operating\s+activities\s+during\s+the\s+quarter\s+"
        rf"was\s+NT\${_NUM_RE}\s+million"
        rf"[\s\S]{{0,140}}?NT\${_NUM_RE}\s+million[\s\S]{{0,80}}?previous\s+quarter"
        rf"[\s\S]{{0,160}}?NT\${_NUM_RE}\s+million[\s\S]{{0,80}}?year-ago\s+quarter",
        re.IGNORECASE,
    )
    cf_m = cf_rgx.search(full_text)
    if cf_m:
        for plabel, v in zip(period_labels, cf_m.groups()):
            try:
                fv = float(v.replace(",", ""))
            except ValueError:
                continue
            emit("operating_cash_flow", "", plabel, fv, "ntd_m")

    # ---- (4) FX rate (transcript-like sentence appears in some PRs) ------
    fx_rgx = re.compile(
        rf"foreign\s+exchange\s+rate\s+applied\s+to\s+the\s+quarter\s+was\s+{_NUM_RE}\s+NT\s+dollar",
        re.IGNORECASE,
    )
    fx_m = fx_rgx.search(full_text)
    if fx_m:
        try:
            fv = float(fx_m.group(1))
            emit("usd_ntd_avg_rate", "", cur_period, fv, "ntd_per_usd")
        except ValueError:
            pass

    bronze = {
        "ticker": ticker,
        "report_period_label": cur_period,
        "report_period_end": parse_period_label(cur_period)[1].isoformat(),
        "periods_in_report": period_labels,
        "source_id": source_id,
        "source_url": source_url,
        "source_pdf_sha256": sha,
        "source_pdf_bytes": len(pdf_bytes),
        "extracted_at": extracted_at,
        "fact_count": len(facts),
        "pages": [{"page": p, "text": t} for p, t in pages_text],
    }
    return bronze, facts


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, period_label: str) -> Path:
    m = _FULL_PERIOD_RE.match(period_label)
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    return BRONZE_ROOT / ticker / str(year) / f"Q{q}" / "press_release.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["report_period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(facts: list[Fact], ticker: str = TICKER) -> Path:
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
        ["period_end", "metric", "dimension"], ascending=[False, True, True],
    )
    combined.to_parquet(out, index=False, compression="zstd")
    return out
