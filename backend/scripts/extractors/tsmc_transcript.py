"""
TSMC earnings-call transcript extractor.

LSEG StreetEvents publishes "edited transcripts" of every TSMC quarterly
earnings call as a PDF. The format is stable across years and across
companies (any LSEG-edited transcript: AAPL, NVDA, MSFT, SMCI, etc. will
parse the same way), so this module is a near-drop-in for other tickers.

Document structure:
  Page 1     — cover page (LSEG branding, event title with date)
  Page 2     — "C O R P O R A T E  P A R T I C I P A N T S"
                "C O N F E R E N C E  C A L L  P A R T I C I P A N T S"
                "P R E S E N T A T I O N"  (begins on this page)
  Pages 3-N  — speaker turns; each turn has a header
                "{Name} - {Company} - {Role}"
                followed by their speech (1+ paragraphs).
                Mid-document the section flips to
                "Q U E S T I O N S  A N D  A N S W E R S"
  Last page  — copyright/disclaimer

Output layers:
  BRONZE: backend/data/financials/raw/{ticker}/{year}/{Q}/transcript.json
          (per-page text + parsed sections + provenance)
  SILVER: backend/data/financials/transcripts/{ticker}.parquet
          (one row per speaker turn, long-format, full-text searchable)
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "financials"
BRONZE_ROOT = DATA_ROOT / "raw"
SILVER_ROOT = DATA_ROOT / "transcripts"


# ---------------------------------------------------------------------------
# Period parsing — distinct from management_report's because LSEG uses
# "Q1 2026" instead of "1Q26".
# ---------------------------------------------------------------------------

# Two formats observed:
#   - LSEG-era (2024+): "Q1 2026 ... Earnings Call ... on April 16, 2026 / 6:00AM"
#   - Refinitiv-era (pre-2024): "Q3 2022 ... Earnings Call ... EVENT DATE/TIME: OCTOBER 13, 2022 / 6:00AM"
# Title in PDF metadata is sometimes empty (e.g. 2022/Q3) — we fall back to
# page-1 text where the same info appears.
_PERIOD_TITLE_RE = re.compile(r"Q(\d)\s*(\d{4})\b", re.IGNORECASE)
_DATE_LSEG_RE   = re.compile(r"\bon\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", re.IGNORECASE)
_DATE_EVENT_RE  = re.compile(r"EVENT\s*DATE(?:/TIME)?[:\s]+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", re.IGNORECASE)
_MONTH_NAMES = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august","september","october","november","december"], start=1)}
_PERIOD_RE = re.compile(r"^(\d)Q(\d{2})$")


def parse_event_metadata(pdf_title: str, page1_text: str) -> dict:
    """Parse cover page / PDF title to get period + event date.

    Handles both LSEG (current) and Refinitiv (pre-2024) layouts. PDF title
    metadata is sometimes empty — falls back to page-1 text content where
    the same info appears.

    Returns dict with: period_label ('1Q26'), period_end (2026-03-31),
    event_date (2026-04-16) or {} if neither pattern matches.
    """
    blob = f"{pdf_title}\n{page1_text}"
    pm = _PERIOD_TITLE_RE.search(blob)
    if not pm:
        return {}
    q = int(pm.group(1))
    year = int(pm.group(2))
    period_label = f"{q}Q{year % 100:02d}"
    eom_month = q * 3
    eom_day = {3: 31, 6: 30, 9: 30, 12: 31}[eom_month]
    out: dict = {
        "period_label": period_label,
        "period_end": date(year, eom_month, eom_day),
    }
    # Try LSEG-era format first, then Refinitiv "EVENT DATE/TIME:" format.
    for rgx in (_DATE_LSEG_RE, _DATE_EVENT_RE):
        dm = rgx.search(blob)
        if dm:
            month_idx = _MONTH_NAMES.get(dm.group(1).lower())
            if month_idx is None:
                continue
            try:
                out["event_date"] = date(int(dm.group(3)), month_idx, int(dm.group(2)))
                break
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------------------
# Section markers — the LSEG layout uses spaced-out headers
# ---------------------------------------------------------------------------

_SECTION_PATTERNS = [
    ("corporate_participants",  re.compile(r"C\s*O\s*R\s*P\s*O\s*R\s*A\s*T\s*E\s+P\s*A\s*R\s*T\s*I\s*C\s*I\s*P\s*A\s*N\s*T\s*S")),
    ("call_participants",        re.compile(r"C\s*O\s*N\s*F\s*E\s*R\s*E\s*N\s*C\s*E\s+C\s*A\s*L\s*L\s+P\s*A\s*R\s*T\s*I\s*C\s*I\s*P\s*A\s*N\s*T\s*S")),
    ("presentation",             re.compile(r"P\s*R\s*E\s*S\s*E\s*N\s*T\s*A\s*T\s*I\s*O\s*N")),
    ("qa",                       re.compile(r"Q\s*U\s*E\s*S\s*T\s*I\s*O\s*N\s*S\s+A\s*N\s*D\s+A\s*N\s*S\s*W\s*E\s*R\s*S")),
    ("disclaimer",               re.compile(r"D\s*I\s*S\s*C\s*L\s*A\s*I\s*M\s*E\s*R")),
]


def _find_section_offsets(full_text: str) -> dict[str, int]:
    """Return a dict mapping section name → character offset in `full_text`."""
    offsets: dict[str, int] = {}
    for name, rgx in _SECTION_PATTERNS:
        m = rgx.search(full_text)
        if m:
            offsets[name] = m.start()
    return offsets


# ---------------------------------------------------------------------------
# Speaker-turn header detection
# ---------------------------------------------------------------------------
#
# Header line shape: "{Name} - {Company} - {Role}"
# Examples:
#   Jeff Su - Taiwan Semiconductor Manufacturing Co Ltd - Director - Investor Relations
#   C.C. Wei - Taiwan Semiconductor Manufacturing Co Ltd - Chairman & Chief Executive Officer
#   Charlie Chan - Morgan Stanley - Analyst
#
# We require:
#   - Line begins with a capital letter
#   - Has at least 2 ' - ' separators (Name - Company - Role minimum)
#   - Total line length is reasonable (8 < N < 200 — not a paragraph)
#   - Doesn't contain typical sentence punctuation like "." mid-line followed
#     by lowercase (heuristic to skip paragraphs that happen to contain " - ")

_HEADER_RE = re.compile(
    r"^([A-Z][\w\.\-' ]+?)\s+-\s+([\w&\.\-,' ]+?)\s+-\s+(.+?)\s*$"
)


def _looks_like_header(line: str) -> tuple[str, str, str] | None:
    line = line.strip()
    if not (8 < len(line) < 220):
        return None
    if line.endswith("."):
        return None
    # Reject paragraph-y lines: contain a period followed by a lowercase letter
    if re.search(r"\.\s+[a-z]", line):
        return None
    m = _HEADER_RE.match(line)
    if not m:
        return None
    name, company, role = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    # Reject if company looks like part of a sentence (lots of stop words)
    if any(stop in company.lower() for stop in [" the ", " and ", " of "]):
        return None
    # Reject "Operator" pseudo-speakers and similar
    if name.lower() in {"operator", "thank you"}:
        return None
    return name, company, role


# ---------------------------------------------------------------------------
# Participant list parser (page 2 typically)
# ---------------------------------------------------------------------------

_PARTICIPANT_RE = re.compile(
    r"^([A-Z][\w\.\-' ]+?)\s+([A-Z][\w&\.\-,' ]+?)\s+-\s+(.+?)\s*$"
)


def _parse_participants(text: str) -> list[dict]:
    """Each participant line on page 2 looks like:
        Jeff Su Taiwan Semiconductor Manufacturing Co Ltd - Director - Investor Relations
        Haas Liu BofA Securities - Analyst
    No dash between Name and Company (unlike speaker turn headers)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        m = _PARTICIPANT_RE.match(line)
        if not m:
            continue
        name, company_role = m.group(1).strip(), (m.group(2) + " - " + m.group(3)).strip()
        out.append({"raw": line, "candidate_name": name, "rest": company_role})
    return out


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


def extract_pdf(
    pdf_path: Path,
    *,
    ticker: str,
    source_url: str | None = None,
) -> tuple[dict, list[TranscriptTurn]]:
    """Extract bronze + silver from one transcript PDF."""
    pdf_bytes = Path(pdf_path).read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pdf_title = doc.metadata.get("title", "") or ""
    pages_text = [(i + 1, p.get_text("text")) for i, p in enumerate(doc)]
    doc.close()
    full_text = "\n".join(t for _, t in pages_text)

    meta = parse_event_metadata(pdf_title, full_text[:4000])
    if not meta:
        raise RuntimeError(
            f"Could not parse period from title or page 1: title={pdf_title!r}"
        )

    section_offsets = _find_section_offsets(full_text)
    pres_start = section_offsets.get("presentation", 0)
    qa_start = section_offsets.get("qa", len(full_text))
    disc_start = section_offsets.get("disclaimer", len(full_text))
    body_end = min(disc_start, len(full_text))
    pres_text = full_text[pres_start:qa_start]
    qa_text = full_text[qa_start:body_end]

    source_id = f"tsmc_earnings_call_{meta['period_label']}"
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Participants (between corporate_participants offset and presentation)
    cp_start = section_offsets.get("corporate_participants", 0)
    participants_block = full_text[cp_start:pres_start] if pres_start else ""
    participants = _parse_participants(participants_block)

    # Speaker turns
    turns: list[TranscriptTurn] = []
    turn_index = 0

    def _ingest(section_label: str, section_text: str) -> None:
        nonlocal turn_index
        # Strip the section header itself off the front
        # (the regex already located the header at the start).
        lines = section_text.splitlines()
        # Skip the first N lines while they're still header-shaped
        i = 0
        while i < len(lines) and (
            not lines[i].strip()
            or _SECTION_PATTERNS[0][1].match(lines[i])
            or _SECTION_PATTERNS[1][1].match(lines[i])
            or _SECTION_PATTERNS[2][1].match(lines[i])
            or _SECTION_PATTERNS[3][1].match(lines[i])
        ):
            i += 1

        current: dict | None = None
        body: list[str] = []

        def _flush() -> None:
            nonlocal turn_index
            if current is None:
                return
            text = "\n".join(body).strip()
            if not text:
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

        for line in lines[i:]:
            hdr = _looks_like_header(line)
            if hdr is not None:
                _flush()
                name, company, role = hdr
                current = {"name": name, "company": company, "role": role}
                body = []
            else:
                if current is not None:
                    body.append(line)
        _flush()

    if pres_text.strip():
        _ingest("presentation", pres_text)
    if qa_text.strip():
        _ingest("qa", qa_text)

    bronze = {
        "ticker": ticker,
        "period_label": meta["period_label"],
        "period_end": meta["period_end"].isoformat(),
        "event_date": meta["event_date"].isoformat() if meta.get("event_date") else None,
        "source_id": source_id,
        "source_url": source_url,
        "source_pdf_sha256": sha,
        "source_pdf_bytes": len(pdf_bytes),
        "pdf_title": pdf_title,
        "extracted_at": extracted_at,
        "participants_raw": participants,
        "section_offsets": section_offsets,
        "turn_count": len(turns),
        "presentation_chars": len(pres_text),
        "qa_chars": len(qa_text),
        "pages": [{"page": p, "text": t} for p, t in pages_text],
    }
    return bronze, turns


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _bronze_path(ticker: str, period_label: str) -> Path:
    m = _PERIOD_RE.match(period_label)
    if not m:
        raise ValueError(f"bad period label: {period_label!r}")
    q = int(m.group(1))
    yy = int(m.group(2))
    year = 2000 + yy if yy < 50 else 1900 + yy
    return BRONZE_ROOT / ticker / str(year) / f"Q{q}" / "transcript.json"


def write_bronze(bronze: dict) -> Path:
    p = _bronze_path(bronze["ticker"], bronze["period_label"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bronze, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def upsert_silver(turns: list[TranscriptTurn], ticker: str) -> Path:
    """Append + dedup on (ticker, period_end, turn_index, source)."""
    if not turns:
        raise RuntimeError("upsert_silver called with no turns")
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    out = SILVER_ROOT / f"{ticker}.parquet"
    new_df = pd.DataFrame([asdict(t) for t in turns])
    if out.exists():
        existing = pd.read_parquet(out)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["ticker", "period_end", "turn_index", "source"], keep="last"
        )
    else:
        combined = new_df
    combined = combined.sort_values(["period_end", "turn_index"], ascending=[False, True])
    combined.to_parquet(out, index=False, compression="zstd")
    return out
