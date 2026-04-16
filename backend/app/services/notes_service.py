"""
NotesService — CRUD and transcript management for MeetingNote.

Deliberately thin: no LLM calls here.
All AI logic lives in meeting_summary_service.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from backend.app.models.domain.meeting_note import MeetingNote, TranscriptLine
from backend.app.models.domain.enums import SummaryStatus
from backend.app.models.orm.note_orm import MeetingNoteORM


class NotesService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_note(
        self,
        tenant_id: str,
        title: str,
        note_type: str,
        company_tickers: List[str],
        meeting_date: Optional[str] = None,
    ) -> MeetingNote:
        note = MeetingNote(
            tenant_id=tenant_id,
            title=title,
            note_type=note_type,
            company_tickers=company_tickers,
            meeting_date=meeting_date,
            editor_content={"type": "doc", "content": []},
        )
        orm = self._to_orm(note)
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return self._to_domain(orm)

    def list_notes(
        self,
        tenant_id: str,
        ticker: Optional[str] = None,
        note_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[MeetingNote]:
        q = self.db.query(MeetingNoteORM).filter(
            MeetingNoteORM.tenant_id == tenant_id
        )
        if note_type:
            q = q.filter(MeetingNoteORM.note_type == note_type)
        rows = q.order_by(MeetingNoteORM.updated_at.desc()).limit(limit).all()

        # Client-side ticker filter (JSON column — avoid DB-level JSON queries for portability)
        if ticker:
            rows = [r for r in rows if ticker.upper() in (r.company_tickers or [])]

        return [self._to_domain(r) for r in rows]

    def get_note(self, note_id: str, tenant_id: str) -> Optional[MeetingNote]:
        row = self._fetch(note_id, tenant_id)
        return self._to_domain(row) if row else None

    def update_note(
        self,
        note_id: str,
        tenant_id: str,
        *,
        title: Optional[str] = None,
        editor_content: Optional[dict] = None,
        editor_plain_text: Optional[str] = None,
        company_tickers: Optional[List[str]] = None,
        meeting_date: Optional[str] = None,
    ) -> Optional[MeetingNote]:
        row = self._fetch(note_id, tenant_id)
        if not row:
            return None
        if title is not None:
            row.title = title
        if editor_content is not None:
            row.editor_content = editor_content
        if editor_plain_text is not None:
            row.editor_plain_text = editor_plain_text
        if company_tickers is not None:
            row.company_tickers = company_tickers
        if meeting_date is not None:
            row.meeting_date = meeting_date
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._to_domain(row)

    def delete_note(self, note_id: str, tenant_id: str) -> bool:
        row = self._fetch(note_id, tenant_id)
        if not row:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------

    def set_recording_started(
        self, note_id: str, tenant_id: str, mode: str, recording_path: str
    ) -> Optional[MeetingNote]:
        row = self._fetch(note_id, tenant_id)
        if not row:
            return None
        row.recording_mode = mode
        row.recording_path = recording_path
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._to_domain(row)

    def save_transcript(
        self,
        note_id: str,
        tenant_id: str,
        transcript_lines: List[dict],
        duration_seconds: int,
    ) -> Optional[MeetingNote]:
        row = self._fetch(note_id, tenant_id)
        if not row:
            return None
        row.transcript_lines = transcript_lines
        row.duration_seconds = duration_seconds
        row.summary_status = SummaryStatus.AWAITING_SPEAKERS.value
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._to_domain(row)

    def flag_transcript_line(
        self, note_id: str, tenant_id: str, line_id: int, flagged: bool
    ) -> Optional[MeetingNote]:
        row = self._fetch(note_id, tenant_id)
        if not row:
            return None
        lines = list(row.transcript_lines or [])
        for line in lines:
            if line.get("line_id") == line_id:
                line["is_flagged"] = flagged
                break
        row.transcript_lines = lines
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._to_domain(row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch(self, note_id: str, tenant_id: str) -> Optional[MeetingNoteORM]:
        return (
            self.db.query(MeetingNoteORM)
            .filter(MeetingNoteORM.note_id == note_id, MeetingNoteORM.tenant_id == tenant_id)
            .first()
        )

    @staticmethod
    def _to_orm(note: MeetingNote) -> MeetingNoteORM:
        return MeetingNoteORM(
            note_id=note.note_id,
            tenant_id=note.tenant_id,
            title=note.title,
            note_type=note.note_type,
            company_tickers=note.company_tickers,
            meeting_date=note.meeting_date,
            created_at=note.created_at,
            updated_at=note.updated_at,
            editor_content=note.editor_content,
            editor_plain_text=note.editor_plain_text,
            recording_path=note.recording_path,
            recording_mode=note.recording_mode,
            duration_seconds=note.duration_seconds,
            transcript_lines=[l.model_dump() for l in note.transcript_lines],
            summary_status=note.summary_status,
            ai_summary=note.ai_summary.model_dump() if note.ai_summary else None,
            fragment_ids=note.fragment_ids,
        )

    @staticmethod
    def _to_domain(row: MeetingNoteORM) -> MeetingNote:
        from backend.app.models.domain.meeting_note import AISummary
        return MeetingNote(
            note_id=row.note_id,
            tenant_id=row.tenant_id,
            title=row.title,
            note_type=row.note_type,
            company_tickers=row.company_tickers or [],
            meeting_date=row.meeting_date,
            created_at=row.created_at,
            updated_at=row.updated_at,
            editor_content=row.editor_content or {},
            editor_plain_text=row.editor_plain_text or "",
            recording_path=row.recording_path,
            recording_mode=row.recording_mode,
            duration_seconds=row.duration_seconds,
            transcript_lines=[
                TranscriptLine(**l) for l in (row.transcript_lines or [])
            ],
            summary_status=SummaryStatus(row.summary_status or "none"),
            ai_summary=AISummary(**row.ai_summary) if row.ai_summary else None,
            fragment_ids=row.fragment_ids or [],
        )
