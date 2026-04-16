"""
Document-level metadata extractor.

Runs ONCE per PDF before the parallel extractors start. The result is shared
between both threads so we pay one LLM call, not two.

Extracts:
  document_title         — the report's full title (e.g. "AI Adoption Tracker: 2025Q4")
  document_main_point    — 1-2 sentence executive summary of the whole document
  document_author        — authoring firm or person (e.g. "Goldman Sachs Research")
  document_date          — publication date in YYYY-MM-DD or "YYYY-MM" if only month known
  source_document_id     — stable UUID5 derived from filename (reproducible across runs)

The source_document_id is a UUID5 seeded from the PDF filename, so the same file
always gets the same ID regardless of when or where it's ingested. This lets any
downstream system JOIN fragments back to their source document without storing a
separate document registry.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.app.interfaces.llm_provider import LLMProvider
from backend.scripts.extractors.pdf_utils import extract_pages_text, chunk_pages

# Stable namespace for source-document UUIDs.
_DOC_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "document_title": {
            "type": "string",
            "description": (
                "The full title of the report exactly as it appears on the cover or header. "
                "Do not abbreviate."
            ),
        },
        "document_main_point": {
            "type": "string",
            "description": (
                "1-2 sentences summarising the document's primary thesis, key finding, or "
                "central argument. This should be dense enough that someone reading only "
                "this sentence understands what the report concludes."
            ),
        },
        "document_author": {
            "type": "string",
            "description": (
                "The firm, team, or individual who produced the document "
                "(e.g. 'Goldman Sachs Global Investment Research', 'Morgan Stanley Equity Research'). "
                "Use 'Unknown' if not found."
            ),
        },
        "document_date": {
            "type": "string",
            "description": (
                "Publication or report date. Prefer YYYY-MM-DD. Use YYYY-MM if only month "
                "is stated, or YYYY if only year. Use 'Unknown' if not found."
            ),
        },
    },
    "required": ["document_title", "document_main_point", "document_author", "document_date"],
}

_METADATA_PROMPT = """\
You are reading the opening pages of a financial research report.

Extract the document metadata listed below and return ONLY valid JSON matching the schema.

Rules:
- document_title: copy the title exactly as written; do not summarise or abbreviate it.
- document_main_point: write 1-2 complete sentences that capture the report's central finding
  or investment thesis. Be specific — include numbers, directions, or entity names where stated.
- document_author: the publishing firm, analyst team, or author name.
- document_date: the publication date in the format described in the schema.

DOCUMENT TEXT (first few pages):
{text}

JSON output:"""


def extract_document_metadata(pdf_path: Path, llm: LLMProvider) -> dict:
    """
    Extracts document-level metadata from the first 3 pages of a PDF.

    Returns a dict with keys:
      document_title, document_main_point, document_author, document_date,
      source_document_id, source_pdf_filename

    Falls back to safe defaults if the LLM call fails — never raises.
    """
    pdf_path = Path(pdf_path)
    filename = pdf_path.name

    # Stable, reproducible ID for this source document
    source_document_id = str(uuid.uuid5(_DOC_NAMESPACE, filename))

    # Read first 3 pages (covers title, author block, executive summary)
    pages = extract_pages_text(pdf_path)
    first_pages = pages[:3]
    if not first_pages:
        return _fallback(filename, source_document_id)

    text = "\n\n".join(p[1] for p in first_pages)
    # Trim to ~4000 chars — enough for a title page + abstract, cheap to embed
    text = text[:4000]

    prompt = _METADATA_PROMPT.format(text=text)

    try:
        result = llm.generate_structured_output(
            prompt=prompt,
            output_schema=_METADATA_SCHEMA,
        )
    except Exception as e:
        print(f"[DocMeta] LLM call failed ({e}), using filename-based fallback.")
        return _fallback(filename, source_document_id)

    return {
        "document_title":      result.get("document_title", filename),
        "document_main_point": result.get("document_main_point", ""),
        "document_author":     result.get("document_author", "Unknown"),
        "document_date":       result.get("document_date", "Unknown"),
        "source_document_id":  source_document_id,
        "source_pdf_filename": filename,
    }


def _fallback(filename: str, source_document_id: str) -> dict:
    return {
        "document_title":      filename,
        "document_main_point": "",
        "document_author":     "Unknown",
        "document_date":       "Unknown",
        "source_document_id":  source_document_id,
        "source_pdf_filename": filename,
    }
