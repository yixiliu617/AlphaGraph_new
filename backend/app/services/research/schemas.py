"""
Pydantic schemas for the document-query research service.

These drive both the LLM structured-output prompt and the API response shape.
Designed so that future source types (earnings call transcripts, meeting
notes) slot in without schema migrations — `source_type` is an enum with
transcript values already reserved.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Source taxonomy
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    PRESS_RELEASE       = "press_release"        # 8-K Item 2.02 EX-99.1
    CFO_COMMENTARY      = "cfo_commentary"       # 8-K Item 2.02 EX-99.2
    MDNA                = "mdna"                 # 10-Q / 10-K MD&A section
    TRANSCRIPT_PREPARED = "transcript_prepared"  # (future) earnings call prepared remarks
    TRANSCRIPT_QA       = "transcript_qa"        # (future) earnings call Q&A
    MEETING_NOTE        = "meeting_note"         # (future) internal notes / meeting notes


# ---------------------------------------------------------------------------
# Quote: verbatim sentence from the source doc
# ---------------------------------------------------------------------------

class Quote(BaseModel):
    text:     str          # exact sentence as it appears in the source
    verified: bool = False # True if the service found this text in the raw source


# ---------------------------------------------------------------------------
# Finding: one extracted answer tied to one source document
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """
    One (document, question) pair produces at most one Finding. If the
    document has nothing relevant to the question, no Finding is emitted
    for that document (we still persist the "no match" sentinel so we
    don't re-extract next time — see ExtractionRow for that).
    """
    finding_id:        str
    ticker:            str
    topic_label:       str   # raw user question
    topic_slug:        str   # normalized key for cache lookup
    source_type:       SourceType
    source_id:         str   # id of the row in earnings_releases (or future transcripts)
    filing_date:       str   # YYYY-MM-DD
    fiscal_period:     Optional[str] = None   # "FY2024-Q1"
    title:             str   # human-readable source title
    source_url:        Optional[str] = None
    key_points:        List[str] = Field(default_factory=list)
    quotes:            List[Quote] = Field(default_factory=list)
    extracted_at:      str   # ISO timestamp
    extractor_model:   str
    extractor_version: str


# ---------------------------------------------------------------------------
# Query request / response
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    ticker:          str
    question:        str
    lookback_years:  int = Field(default=3, ge=1, le=10)
    source_types:    Optional[List[SourceType]] = None  # None = all available


class QueryResponse(BaseModel):
    ticker:          str
    question:        str
    topic_slug:      str
    lookback_years:  int
    generated_at:    str
    findings:        List[Finding]
    docs_considered: int
    docs_with_hits:  int
    from_cache:      int    # how many findings were served from persisted store
    newly_extracted: int    # how many were freshly run through the LLM


# ---------------------------------------------------------------------------
# LLM structured output schema
# The LLM returns a list of per-document results. For each doc it either
# returns a non-empty key_points + quotes list, or sets relevant=false.
# ---------------------------------------------------------------------------

LLM_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id":   {"type": "string", "description": "The source_id passed in with this document"},
                    "relevant":    {"type": "boolean", "description": "True if the document contains content relevant to the question"},
                    "key_points":  {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-5 one-sentence summaries of what management said about the topic. Empty list if not relevant.",
                    },
                    "quotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Verbatim sentence from the source document, exactly as written"},
                            },
                            "required": ["text"],
                        },
                        "description": "Direct, verbatim quotes from the source document supporting the key_points. Must appear literally in the source text. Empty list if not relevant.",
                    },
                },
                "required": ["source_id", "relevant", "key_points", "quotes"],
            },
        }
    },
    "required": ["results"],
}
