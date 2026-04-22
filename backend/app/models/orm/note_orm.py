"""
SQLAlchemy ORM model for MeetingNote.
Uses the same Base as fragment_orm so create_all covers all tables in one call.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from backend.app.models.orm.fragment_orm import Base


class MeetingNoteORM(Base):
    __tablename__ = "meeting_notes"

    note_id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    note_type = Column(String, nullable=False)

    # JSON list of ticker strings, e.g. ["NVDA", "AMD"]
    company_tickers = Column(JSON, default=list)

    # ISO date string "YYYY-MM-DD"
    meeting_date = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    # Tiptap JSON doc
    editor_content = Column(JSON, default=dict)
    # Plain text extracted from Tiptap JSON for search
    editor_plain_text = Column(Text, default="")

    # Recording
    recording_path = Column(String, nullable=True)
    recording_mode = Column(String, nullable=True)   # "wasapi" | "browser"
    duration_seconds = Column(Integer, nullable=True)

    # JSON list of TranscriptLine dicts
    transcript_lines = Column(JSON, default=list)

    # Polished transcript from the post-meeting Gemini pass
    polished_transcript = Column(Text, nullable=True)
    polished_transcript_language = Column(String, nullable=True)
    polished_transcript_meta = Column(JSON, nullable=True)

    # A/B experiment variant — "A" = classic (wizard in sidebar),
    # "B" = new (transcripts in editor + chat). Default A so existing code paths
    # are unchanged for every pre-existing and newly-created note unless the
    # caller opts into B.
    ux_variant = Column(String, default="A", nullable=False)

    # AI summary flow state
    summary_status = Column(String, default="none")

    # Full AISummary dict (topic_fragments, delta_cards, action_items, …)
    ai_summary = Column(JSON, nullable=True)

    # List of DataFragment UUIDs (strings) spawned from this note
    fragment_ids = Column(JSON, default=list)
