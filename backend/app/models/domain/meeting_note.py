"""
MeetingNote domain model.

A MeetingNote is the central object for the Notes tab. It contains:
  - The user's rich-text notes (Tiptap JSON + plain text for search)
  - An optional audio recording + live transcript
  - An AI summary produced by the post-meeting wizard:
      speaker labels -> user topics -> per-topic DataFragments
      -> delta comparison with previous meetings -> user approval

One DataFragment is created per topic (not per sentence).
Each fragment's content.extracted_metrics.supporting_sentences contains
all relevant sentences, timestamps, speaker labels, and relevance reasons.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, Field

from backend.app.models.domain.enums import RecordingMode, SummaryStatus


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class TranscriptLine(BaseModel):
    """Single utterance line from the live transcript stream."""
    line_id: int
    timestamp: str           # "HH:MM:SS"
    speaker_label: str       # Deepgram-assigned: "Speaker 0", "Speaker 1", …
    speaker_name: Optional[str] = None   # Assigned by user later: "John Smith (CFO)"
    text: str
    is_flagged: bool = False   # User clicked the flag icon during live recording
    is_interim: bool = False   # True while Deepgram is still processing the utterance


class SpeakerMapping(BaseModel):
    """Maps Deepgram speaker label to a human-readable name + role."""
    label: str           # "Speaker 0"
    name: str            # "John Smith"
    role: Optional[str] = None   # "CFO", "IR", "CEO", …


# ---------------------------------------------------------------------------
# AI Summary objects
# ---------------------------------------------------------------------------

class SupportingSentence(BaseModel):
    """
    One sentence (or utterance) from the transcript that was deemed relevant
    to a topic during post-meeting extraction.
    """
    sentence_id: int           # line_id in transcript_lines
    timestamp: str             # "HH:MM:SS"
    speaker: str               # Resolved name if available, else label
    text: str
    relevance_reason: str      # Why the LLM flagged this as related to the topic
    has_number: bool = False
    numbers: List[str] = Field(default_factory=list)  # e.g. ["68-70%", "$2.1B"]


class TopicFragment(BaseModel):
    """
    Encapsulates everything extracted for one user-defined topic.
    Maps 1-to-1 with the DataFragment stored in the DB.
    """
    topic: str
    topic_summary: str                          # LLM-generated narrative for the topic
    supporting_sentences: List[SupportingSentence] = Field(default_factory=list)
    overall_tone: str = ""                      # "bullish", "cautious", "neutral", "bearish"
    direction: str = ""                         # "improving", "stable", "declining"
    key_numbers: List[str] = Field(default_factory=list)
    speakers_involved: List[str] = Field(default_factory=list)
    fragment_id: Optional[str] = None          # Set after saving to DB


class DeltaCard(BaseModel):
    """
    Represents a meaningful change detected between this meeting and a previous one
    on the same topic.  User can Approve -> creates MEETING_DELTA fragment,
    Edit -> modify then save, or Dismiss -> discard.
    """
    delta_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic: str
    previous_statement: str
    previous_source: str    # e.g. "Q3 FY25 Earnings Call — Jan 15 2025"
    current_statement: str
    change_type: str        # "SIGNIFICANT" | "NUMBER_CHANGE" | "TONE_SHIFT" | "NEW_RISK" | "RESOLVED"
    significance: str       # "HIGH" | "MEDIUM" | "LOW"
    status: str = "PENDING" # "PENDING" | "APPROVED" | "EDITED" | "DISMISSED"
    edited_text: Optional[str] = None    # Only set if user edited before approving
    approved_fragment_id: Optional[str] = None


class AISummary(BaseModel):
    """
    Full output of the post-meeting AI wizard.
    Persisted as JSON on the MeetingNoteORM row.
    """
    speaker_mappings: List[SpeakerMapping] = Field(default_factory=list)
    user_topics: List[str] = Field(default_factory=list)
    topic_fragments: List[TopicFragment] = Field(default_factory=list)
    delta_cards: List[DeltaCard] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    note_enhancements: List[str] = Field(default_factory=list)
    ai_narrative: str = ""       # Short executive summary paragraph


# ---------------------------------------------------------------------------
# Detailed meeting summary (produced by the Gemini polish call alongside the
# polished transcript; stored in polished_transcript_meta.summary).
# ---------------------------------------------------------------------------

class SubPoint(BaseModel):
    """One sub-point under a key point. The supporting field is a 2-3 sentence
    argument backing the sub-point, grounded in what was said in the meeting."""
    text: str
    supporting: str


class KeyPoint(BaseModel):
    """A top-level point in the meeting storyline, with its supporting sub-points."""
    title: str
    sub_points: List[SubPoint] = Field(default_factory=list)


class FinancialMetrics(BaseModel):
    """Revenue/profit/order-specific mentions pulled out of the transcript for
    quick analyst scanning. Each list contains short strings like
    'Q1 revenue $2.1B, up 20% YoY'."""
    revenue: List[str] = Field(default_factory=list)
    profit: List[str] = Field(default_factory=list)
    orders: List[str] = Field(default_factory=list)


class MeetingSummary(BaseModel):
    """Detailed structured summary produced by Gemini at polish time.
    Rendered into the main editor (between user notes and raw transcript)."""
    storyline: str = ""
    key_points: List[KeyPoint] = Field(default_factory=list)
    all_numbers: List[str] = Field(default_factory=list)
    recent_updates: List[str] = Field(default_factory=list)
    financial_metrics: FinancialMetrics = Field(default_factory=FinancialMetrics)


# ---------------------------------------------------------------------------
# Core domain model
# ---------------------------------------------------------------------------

class MeetingNote(BaseModel):
    note_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    title: str
    note_type: str
    company_tickers: List[str] = Field(default_factory=list)
    meeting_date: Optional[str] = None     # ISO "YYYY-MM-DD"

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Tiptap JSON (stored as-is for the editor) + plain text for full-text search
    editor_content: Dict[str, Any] = Field(default_factory=dict)
    editor_plain_text: str = ""

    # A/B experiment variant — see ORM comment for full context.
    ux_variant: Literal["A", "B"] = "A"

    # Recording metadata
    recording_path: Optional[str] = None
    recording_mode: Optional[RecordingMode] = None
    duration_seconds: Optional[int] = None
    transcript_lines: List[TranscriptLine] = Field(default_factory=list)

    # Polished transcript from the post-meeting Gemini pass
    polished_transcript: Optional[str] = None
    polished_transcript_language: Optional[str] = None
    polished_transcript_meta: Optional[Dict[str, Any]] = None

    # AI post-meeting flow
    summary_status: SummaryStatus = SummaryStatus.NONE
    ai_summary: Optional[AISummary] = None

    # IDs of DataFragments (MEETING_NOTE + MEETING_DELTA) spawned from this note
    fragment_ids: List[str] = Field(default_factory=list)
