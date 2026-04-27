"""
MediaTek (2454.TW) earnings-call transcript extractor.

MediaTek publishes its own English transcript starting 2021Q2 (pre-2021Q2:
no transcript; 2021Q1: 'Prepared-remark.pdf' only — different format,
deferred). Producer is Microsoft Word. Layout differs from TSMC's LSEG
transcripts:

  - No spaced-letter section headers. Plain text "PREPARED REMARKS"
    marks the prepared section; Q&A starts with the first "Q – {Name},
    {Firm}" line.
  - Speaker turn header is "{Name}, {Role}" (comma-separated, NOT
    "{Name} - {Company} - {Role}" dash-separated like LSEG).
  - Q&A speaker syntax: "Q – {Name}, {Firm}" for analyst questions and
    "A – {Speaker} ({Role})" for management answers.
  - The em-dash "–" in Q/A markers is the smart-Unicode em-dash
    (U+2013), not a hyphen.

Output layers (mirrors TSMC):
  BRONZE: backend/data/financials/raw/2454.TW/{YYYY}/Q{N}/transcript.json
  SILVER: backend/data/financials/transcripts/2454.TW.parquet
          (one row per speaker turn, long-format, full-text searchable)

Plus a structured-guidance extractor that parses the explicit forward
guidance MediaTek's CFO reads near the end of the prepared remarks
("Moving to the guidance, in the first quarter of 2026, we expect ...").
This populates `backend/data/financials/guidance/2454.TW.parquet` with
the same schema TSMC + UMC use.
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

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "transcripts"
GUIDANCE_ROOT = DATA_ROOT / "guidance"

TICKER = "2454.TW"


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------
#
# Transcript page 1 looks like:
#     "1
#      MediaTek 4Q25 Earnings Call
#      Wednesday, February 4, 2026, 3:00pm Taiwan Time
#      PREPARED REMARKS"
#
# We grab the period from the title line directly.

_PERIOD_TITLE_RE = re.compile(r"MediaTek\s+(\d)Q(\d{2})\s+Earnings\s+Call", re.IGNORECASE)
_DATE_RE = re.compile(
    r"^[A-Za-z]+,\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", re.MULTILINE,
)
_FULL_PERIOD_RE = re.compile(r"^(\d)Q(\d{2})$")
_MONTH_NAMES = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august","september","october","november","december"], start=1)}


def parse_event_metadata(full_text: str) -> dict:
    pm = _PERIOD_TITLE_RE.search(full_text[:1000])
    if not pm:
        return {}
    q = int(pm.group(1))
    yy = int(pm.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    period_label = f"{q}Q{yy:02d}"
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    out: dict = {
        "period_label": period_label,
        "period_end": date(year, eom_month, eom_day),
    }
    dm = _DATE_RE.search(full_text[:2000])
    if dm:
        month = _MONTH_NAMES.get(dm.group(1).lower())
        if month is not None:
            try:
                out["event_date"] = date(int(dm.group(3)), month, int(dm.group(2)))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------
# Speaker-turn detection
# ---------------------------------------------------------------------------

# Prepared-remarks headers: "Jessie Wang, IR Deputy Director" /
# "David Ku, Chief Financial Officer" / "Dr. Rick Tsai, Chief Executive Officer"
_PREPARED_HEADER_RE = re.compile(
    r"^(?:Dr\.\s+|Mr\.\s+)?([A-Z][A-Za-z][A-Za-z\.\s'-]{1,30}[A-Za-z]),\s+([A-Za-z][\w&\s\-]{2,60})\s*$"
)

# Q&A section headers (the dash is em-dash U+2013 in current MediaTek
# transcripts; older transcripts may use a regular hyphen):
_QA_QUESTION_RE = re.compile(
    r"^Q\s*[–-]\s*(?:Dr\.\s+|Mr\.\s+)?([A-Z][\w\.\s'-]{1,40}),\s+([A-Z][\w &\.\-/]+?)\s*$"
)
_QA_ANSWER_RE = re.compile(
    r"^A\s*[–-]\s*(?:Dr\.\s+|Mr\.\s+)?([A-Z][\w\.\s'-]{1,40}),\s+(.+?)\s*$"
)

# Reject lines that look header-y but are actually paragraph fragments
_REJECT_FRAGMENT_RE = re.compile(r"\.\s+[a-z]")


def _looks_like_prepared_header(line: str) -> tuple[str, str, str] | None:
    s = line.strip()
    if not (10 < len(s) < 120):
        return None
    if _REJECT_FRAGMENT_RE.search(s):
        return None
    m = _PREPARED_HEADER_RE.match(s)
    if not m:
        return None
    name, role = m.group(1).strip(), m.group(2).strip()
    # Reject prose like "MediaTek, Inc. announced today..." — role mustn't
    # start with a verb-ish token.
    if any(role.lower().startswith(w) for w in ["a ", "the ", "an ", "in ", "on ", "of ", "and "]):
        return None
    return name, "MediaTek Inc", role


def _looks_like_qa_question(line: str) -> tuple[str, str, str] | None:
    s = line.strip()
    if not s.startswith("Q"):
        return None
    m = _QA_QUESTION_RE.match(s)
    if not m:
        return None
    name, firm = m.group(1).strip(), m.group(2).strip()
    return name, firm, "Analyst"


def _looks_like_qa_answer(line: str) -> tuple[str, str, str] | None:
    s = line.strip()
    if not s.startswith("A"):
        return None
    m = _QA_ANSWER_RE.match(s)
    if not m:
        return None
    name, role = m.group(1).strip(), m.group(2).strip()
    return name, "MediaTek Inc", role


# ---------------------------------------------------------------------------
# Top-level extractor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TranscriptTurn:
    ticker: str
    period_end: date
    period_label: str
    event_date: date | None
    source: str
    turn_index: int
    section: str
    speaker_name: str
    speaker_company: str
    speaker_role: str
    text: str
    char_count: int
    extracted_at: str


def extract_transcript(
    pdf_path: Path,
    *,
    ticker: str = TICKER,
    source_url: str | None = None,
) -> tuple[dict, list[TranscriptTurn]]:
    pdf_bytes = Path(pdf_path).read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    doc.close()
    full_text = "\n".join(t for _, t in pages_text)

    meta = parse_event_metadata(full_text)
    if not meta:
        raise RuntimeError(
            "Could not parse period from transcript title (expected 'MediaTek {N}Q{YY} Earnings Call')"
        )
    source_id = f"mediatek_earnings_call_{meta['period_label']}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Section split: "PREPARED REMARKS" → first "Q – ..." marks Q&A start
    pres_start_m = re.search(r"\bPREPARED\s+REMARKS\b", full_text, re.IGNORECASE)
    pres_start = pres_start_m.end() if pres_start_m else 0
    qa_match = None
    for m in re.finditer(r"^Q\s*[–-]\s+", full_text[pres_start:], re.MULTILINE):
        qa_match = m
        break
    qa_start = pres_start + qa_match.start() if qa_match else len(full_text)
    pres_text = full_text[pres_start:qa_start]
    qa_text = full_text[qa_start:]

    turns: list[TranscriptTurn] = []
    turn_index = 0

    def _flush(current: dict | None, body: list[str], section_label: str) -> None:
        nonlocal turn_index
        if current is None:
            return
        text = "\n".join(body).strip()
        # Drop very short noise turns ("Thank you." etc.) under 12 chars
        if len(text) < 12:
            return
        turns.append(TranscriptTurn(
            ticker=ticker,
            period_end=meta["period_end"],
            period_label=meta["period_label"],
            event_date=meta.get("event_date"),
            source=source_id,
            turn_index=turn_index,
            section=section_label,
            speaker_name=current["name"],
            speaker_company=current["company"],
            speaker_role=current["role"],
            text=text,
            char_count=len(text),
            extracted_at=extracted_at,
        ))
        turn_index += 1

    def _ingest(section_label: str, section_text: str, header_detector) -> None:
        current: dict | None = None
        body: list[str] = []
        for line in section_text.splitlines():
            hdr = header_detector(line)
            if hdr is not None:
                _flush(current, body, section_label)
                name, company, role = hdr
                current = {"name": name, "company": company, "role": role}
                body = []
            else:
                if current is not None:
                    body.append(line)
        _flush(current, body, section_label)

    if pres_text.strip():
        _ingest("presentation", pres_text, _looks_like_prepared_header)
    if qa_text.strip():
        # In Q&A, alternate question/answer detection
        def _qa_header(line: str):
            return _looks_like_qa_question(line) or _looks_like_qa_answer(line)
        _ingest("qa", qa_text, _qa_header)

    bronze = {
        "ticker": ticker,
        "period_label": meta["period_label"],
        "period_end": meta["period_end"].isoformat(),
        "event_date": meta["event_date"].isoformat() if meta.get("event_date") else None,
        "source_id": source_id,
        "source_url": source_url,
        "source_pdf_sha256": sha,
        "source_pdf_bytes": len(pdf_bytes),
        "extracted_at": extracted_at,
        "turn_count": len(turns),
        "presentation_chars": len(pres_text),
        "qa_chars": len(qa_text),
        "pages": [{"page": p, "text": t} for p, t in pages_text],
    }
    return bronze, turns


# ---------------------------------------------------------------------------
# Forward guidance extraction
# ---------------------------------------------------------------------------
#
# MediaTek's CFO reads explicit forward guidance near the end of the
# prepared remarks. The structured patterns we extract:
#
#   "first quarter revenue to be in the range of NT$141.2 billion dollars
#    to NT$150.2 billion dollars"
#     -> revenue, low + high in NT$ B
#
#   "Gross margin is forecasted at 46%, plus or minus 1.5 percentage points"
#     -> gross_margin, point + spread (low/high derived as point ± spread)
#
#   "forecasted exchange rate of 31.2 NT dollars to 1 US dollar"
#     -> usd_ntd_avg_rate point
#
# Plus we capture the verbal text for each metric so the dashboard can
# show the management's exact words.

_GUIDE_REVENUE_RE = re.compile(
    # Anchor on "in the range of NT$" — the prefix phrasing varies across
    # transcripts ("our first quarter revenue to be in the range of...",
    # "For the fourth quarter, we expect revenue to be in the range of...",
    # "we now expect our first quarter revenue to be in the range of..."),
    # but every revenue guidance sentence ends with two NT$ amounts in this
    # exact form. The optional space before "billion" handles the typo
    # "NT$ 101.7billion" (no space) seen in the 4Q22 transcript.
    r"in\s+the\s+range\s+of\s+NT\$\s*([\d.]+)\s*billion\s+dollars\s+to\s+NT\$\s*([\d.]+)\s*billion\s+dollars",
    re.IGNORECASE,
)
_GUIDE_GM_RE = re.compile(
    # Variants seen across MediaTek transcripts:
    #   "Gross margin is forecasted at 46%, plus or minus 1.5 percentage points"  (default)
    #   "Gross margin for the third quarter is forecasted at 47%, plus or minus 1.5 percentage points"  (2Q25)
    # The optional "for the (Nth) quarter" interjects between "margin" and
    # "is forecasted at".
    r"[Gg]ross\s+margin\s+(?:for\s+the\s+(?:first|second|third|fourth)\s+quarter\s+)?"
    r"is\s+forecasted\s+at\s+([\d.]+)%,?\s+plus\s+or\s+minus\s+([\d.]+)\s+percentage\s+points?",
)
_GUIDE_FX_RE = re.compile(
    r"forecasted\s+exchange\s+rate\s+of\s+([\d.]+)\s+NT\s+dollars?\s+to\s+1\s+US\s+dollar",
    re.IGNORECASE,
)


def _next_quarter_label(period_label: str) -> str:
    m = _FULL_PERIOD_RE.match(period_label)
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    total = year * 4 + q  # next quarter
    ny, nq_idx = divmod(total, 4)
    return f"{nq_idx + 1}Q{str(ny)[2:]}"


# Presentation-PDF guidance — used as a FALLBACK when the transcript is
# missing or mislabeled at source. The Business Outlook slide (typically
# page ~10-11) carries the same numeric guidance the CFO reads at the end
# of the prepared remarks, in a more compact form:
#
#   "Business Outlook
#    For 2023-Q4, we currently expect:
#    Consolidated revenue: Around NT$120 billion ~ 126.6 billion,
#    at a forecast exchange rate of 32 NT dollars to 1 US dollar
#    Consolidated gross margin: 47% ± 1.5%"

_PRES_PERIOD_RE = re.compile(r"For\s+(\d{4})[-\s]*Q(\d)\s*,\s*we\s+currently\s+expect", re.IGNORECASE)
_PRES_REV_RE = re.compile(
    r"[Cc]onsolidated\s+revenue\s*:?\s*Around\s+NT\$\s*([\d.]+)\s*billion\s*[~\-–]\s*([\d.]+)\s*billion",
)
_PRES_FX_RE = re.compile(
    r"forecast\s+exchange\s+rate\s+of\s+([\d.]+)\s+NT\s+dollars?\s+to\s+1\s+US\s+dollar",
    re.IGNORECASE,
)
_PRES_GM_RE = re.compile(
    r"[Cc]onsolidated\s+gross\s+margin\s*:?\s*([\d.]+)%\s*[±\+\/\-]+\s*([\d.]+)%",
)
_PRES_OPEX_RE = re.compile(
    r"[Cc]onsolidated\s+operating\s+expense\s+ratio\s*:?\s*([\d.]+)%\s*[±\+\/\-]+\s*([\d.]+)%",
)


def extract_guidance_from_presentation(
    pdf_path: Path,
    *,
    ticker: str = TICKER,
    issued_in_period_label: str | None = None,
) -> list[dict]:
    """Fallback: extract guidance from MediaTek's Investor Conference
    Presentation slide (Business Outlook page).

    Use when the transcript is unavailable or mislabeled at source. The
    presentation carries the same numeric guidance as the transcript prose,
    with periods explicitly named (`"For 2023-Q4, we currently expect"`).

    Args:
        pdf_path: path to the presentation PDF.
        issued_in_period_label: optional override; if not given, derived
                               from the for-period found in the slide
                               (issuing report = quarter just before).
    """
    pdf_bytes = Path(pdf_path).read_bytes()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(p.get_text("text") for p in doc)
    doc.close()

    pm = _PRES_PERIOD_RE.search(full_text)
    if not pm:
        return []
    for_year = int(pm.group(1))
    for_q = int(pm.group(2))
    for_period = f"{for_q}Q{for_year % 100:02d}"
    if not issued_in_period_label:
        # Issuing report = previous quarter
        prev_total = for_year * 4 + (for_q - 1) - 1
        py, pq = divmod(prev_total, 4)
        issued_in_period_label = f"{pq + 1}Q{py % 100:02d}"

    source_id = f"mediatek_presentation_{issued_in_period_label}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict] = []

    def _emit(metric: str, bound: str, value: float | None, unit: str, text: str) -> None:
        records.append({
            "ticker": ticker,
            "issued_in_period_label": issued_in_period_label,
            "for_period_label": for_period,
            "metric": metric,
            "bound": bound,
            "value": value,
            "unit": unit,
            "text": text,
            "source": source_id,
            "extracted_at": extracted_at,
        })

    rm = _PRES_REV_RE.search(full_text)
    if rm:
        lo, hi = float(rm.group(1)), float(rm.group(2))
        text = rm.group(0)
        _emit("guidance_revenue", "verbal", None, "ntd_b", text)
        _emit("guidance_revenue", "low",  lo, "ntd_b", text)
        _emit("guidance_revenue", "high", hi, "ntd_b", text)
        _emit("guidance_revenue", "midpoint", (lo + hi) / 2.0, "ntd_b", text)

    gm = _PRES_GM_RE.search(full_text)
    if gm:
        point, spread = float(gm.group(1)), float(gm.group(2))
        text = gm.group(0)
        _emit("guidance_gross_margin", "verbal", None, "pct", text)
        _emit("guidance_gross_margin", "low",  point - spread, "pct", text)
        _emit("guidance_gross_margin", "high", point + spread, "pct", text)
        _emit("guidance_gross_margin", "midpoint", point, "pct", text)
        _emit("guidance_gross_margin", "point", point, "pct", text)

    fx = _PRES_FX_RE.search(full_text)
    if fx:
        rate = float(fx.group(1))
        text = fx.group(0)
        _emit("guidance_usd_ntd_avg_rate", "verbal", None, "ntd_per_usd", text)
        _emit("guidance_usd_ntd_avg_rate", "point", rate, "ntd_per_usd", text)
        _emit("guidance_usd_ntd_avg_rate", "midpoint", rate, "ntd_per_usd", text)

    return records


def extract_guidance(
    pdf_path: Path,
    *,
    ticker: str = TICKER,
) -> list[dict]:
    """Extract structured forward guidance + verbal records from a transcript."""
    pdf_bytes = Path(pdf_path).read_bytes()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(p.get_text("text") for p in doc)
    doc.close()

    meta = parse_event_metadata(full_text)
    if not meta:
        return []
    cur_period = meta["period_label"]
    next_period = _next_quarter_label(cur_period)
    source_id = f"mediatek_earnings_call_{cur_period}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    records: list[dict] = []

    def _emit(metric: str, bound: str, value: float | None, unit: str, text: str) -> None:
        records.append({
            "ticker": ticker,
            "issued_in_period_label": cur_period,
            "for_period_label": next_period,
            "metric": metric,
            "bound": bound,
            "value": value,
            "unit": unit,
            "text": text,
            "source": source_id,
            "extracted_at": extracted_at,
        })

    # Revenue range
    rm = _GUIDE_REVENUE_RE.search(full_text)
    if rm:
        lo = float(rm.group(1))
        hi = float(rm.group(2))
        text = rm.group(0)
        _emit("guidance_revenue", "verbal", None, "ntd_b", text)
        _emit("guidance_revenue", "low", lo, "ntd_b", text)
        _emit("guidance_revenue", "high", hi, "ntd_b", text)
        _emit("guidance_revenue", "midpoint", (lo + hi) / 2.0, "ntd_b", text)

    # Gross margin: point ± spread
    gm = _GUIDE_GM_RE.search(full_text)
    if gm:
        point = float(gm.group(1))
        spread = float(gm.group(2))
        text = gm.group(0)
        _emit("guidance_gross_margin", "verbal", None, "pct", text)
        _emit("guidance_gross_margin", "low", point - spread, "pct", text)
        _emit("guidance_gross_margin", "high", point + spread, "pct", text)
        _emit("guidance_gross_margin", "midpoint", point, "pct", text)
        _emit("guidance_gross_margin", "point", point, "pct", text)

    # FX rate (single point)
    fx = _GUIDE_FX_RE.search(full_text)
    if fx:
        rate = float(fx.group(1))
        text = fx.group(0)
        _emit("guidance_usd_ntd_avg_rate", "verbal", None, "ntd_per_usd", text)
        _emit("guidance_usd_ntd_avg_rate", "point", rate, "ntd_per_usd", text)
        _emit("guidance_usd_ntd_avg_rate", "midpoint", rate, "ntd_per_usd", text)

    return records


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, period_label: str) -> Path:
    m = _FULL_PERIOD_RE.match(period_label)
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    return BRONZE_ROOT / ticker / str(year) / f"Q{q}" / "transcript.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(turns: list[TranscriptTurn], ticker: str = TICKER) -> Path:
    if not turns:
        raise RuntimeError("upsert_silver called with no turns")
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    out = SILVER_ROOT / f"{ticker}.parquet"
    new_df = pd.DataFrame([asdict(t) for t in turns])
    if out.exists():
        existing = pd.read_parquet(out)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["ticker", "period_end", "turn_index", "source"], keep="last",
        )
    else:
        combined = new_df
    combined = combined.sort_values(["period_end", "turn_index"], ascending=[False, True])
    combined.to_parquet(out, index=False, compression="zstd")
    return out


def upsert_guidance(records: list[dict], ticker: str = TICKER) -> Path | None:
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
