"""
MeetingSummaryService — 5-step post-meeting AI wizard.

Step 0: Speaker labeling        (user provides names -> saved to note)
Step 1: Topic elicitation       (user provides topics + LLM suggests extras)
Step 2: Topic extraction        (LLM extracts one fragment per topic from transcript)
Step 3: Delta comparison        (LLM compares with previous fragments for same co+topic)
Step 4: User approval           (user approves/edits/dismisses each DeltaCard)
Step 5: Note enhancements       (LLM suggests additions to user's written notes)

Each step updates summary_status on the MeetingNote and returns the note.
Fragment creation happens during Step 2 approval.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.db_repository import DBRepository
from backend.app.models.domain.enums import (
    NoteType, SourceType, SummaryStatus, TenantTier
)
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.meeting_note import (
    AISummary, DeltaCard, MeetingNote, SpeakerMapping,
    SupportingSentence, TopicFragment,
)
from backend.app.models.orm.note_orm import MeetingNoteORM


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

_TOPIC_SUGGESTION_PROMPT = """
You are a financial analyst assistant. The following is a partial transcript
from a {note_type} meeting about {tickers}.

First 500 words of transcript:
{transcript_excerpt}

List 5-8 important financial or business topics discussed in this meeting.
Return as a JSON array of short strings, e.g.:
["gross margin", "capex guidance", "China demand", "inventory levels"]
Only return the JSON array, nothing else.
"""

_TOPIC_DERIVATION_PROMPT = """
You are a financial analyst assistant. Derive the 3-6 key topics the user cares
about for this {note_type} meeting about {tickers}. Prioritise what the user
took notes on; use the transcript only to fill gaps.

USER'S OWN NOTES (authoritative — these reflect the user's focus):
{user_notes}

TRANSCRIPT EXCERPT (first ~600 words, for context only):
{transcript_excerpt}

Return a JSON array of 3-6 short topic strings (each 1-4 words, lower case).
Only return the JSON array, nothing else.
"""

_TOPIC_EXTRACTION_PROMPT = """
You are an expert financial analyst. Extract all statements related to the topic
"{topic}" from this meeting transcript.

Transcript (with line IDs and timestamps):
{transcript_text}

For each relevant sentence/utterance, capture:
- sentence_id (the line_id from the transcript)
- timestamp
- speaker (use resolved name if available, else label)
- text (exact quote)
- relevance_reason (1 sentence explaining why this is relevant to "{topic}")
- has_number (true/false)
- numbers (list of numeric values or percentages mentioned, empty list if none)

Then produce:
- topic_summary: A 2-3 sentence narrative summarizing management's overall view on "{topic}"
- overall_tone: one of "bullish", "cautious", "neutral", "bearish"
- direction: one of "improving", "stable", "declining", "mixed"
- key_numbers: list of the most important numbers mentioned
- speakers_involved: list of unique speaker names/labels who spoke about this topic

Return ONLY valid JSON matching this exact structure:
{{
  "topic": "{topic}",
  "topic_summary": "...",
  "supporting_sentences": [...],
  "overall_tone": "...",
  "direction": "...",
  "key_numbers": [...],
  "speakers_involved": [...]
}}
"""

_DELTA_COMPARISON_PROMPT = """
You are comparing what management said about "{topic}" in two different meetings.

PREVIOUS (from {previous_source}):
{previous_text}

CURRENT (from today's meeting):
{current_text}

Identify meaningful changes. For each change found, return JSON with:
- change_type: "SIGNIFICANT" | "NUMBER_CHANGE" | "TONE_SHIFT" | "NEW_RISK" | "RESOLVED"
- significance: "HIGH" | "MEDIUM" | "LOW"
- previous_statement: concise summary of what was said before
- current_statement: concise summary of what was said now

Return a JSON array of change objects. If no meaningful change, return an empty array [].
"""

_NOTE_ENHANCEMENT_PROMPT = """
The user wrote these notes during the meeting:
{user_notes}

The transcript contained these additional important points not covered in the notes:
{uncovered_points}

List 2-4 specific additions the user might want to add to their notes.
Return a JSON array of short strings. Each string should be a complete sentence
describing the addition. Return only the JSON array.
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MeetingSummaryService:
    def __init__(self, db: Session, db_repo: DBRepository, llm: LLMProvider):
        self.db = db
        self.db_repo = db_repo
        self.llm = llm

    # ------------------------------------------------------------------
    # Step 0: Save speaker mappings
    # ------------------------------------------------------------------

    def save_speaker_mappings(
        self,
        note_id: str,
        tenant_id: str,
        mappings: List[Dict[str, str]],
    ) -> MeetingNote:
        """
        Receives: [{"label": "Speaker 0", "name": "John Smith", "role": "CFO"}, ...]
        Updates transcript lines to replace label with resolved name.
        """
        row = self._fetch_row(note_id, tenant_id)
        speaker_map = {m["label"]: m for m in mappings}

        # Resolve names in transcript lines
        lines = list(row.transcript_lines or [])
        for line in lines:
            label = line.get("speaker_label", "")
            if label in speaker_map:
                m = speaker_map[label]
                line["speaker_name"] = f"{m['name']}{' (' + m['role'] + ')' if m.get('role') else ''}"

        row.transcript_lines = lines

        summary = row.ai_summary or {}
        summary["speaker_mappings"] = mappings
        row.ai_summary = summary
        row.summary_status = SummaryStatus.AWAITING_TOPICS.value
        row.updated_at = datetime.utcnow()
        self.db.commit()
        return self._row_to_domain(row)

    # ------------------------------------------------------------------
    # Step 1: Suggest additional topics (called before user finalises topics)
    # ------------------------------------------------------------------

    def suggest_topics(self, note_id: str, tenant_id: str) -> List[str]:
        """Returns LLM-suggested topics from the first ~500 words of transcript."""
        row = self._fetch_row(note_id, tenant_id)
        lines = row.transcript_lines or []
        excerpt = " ".join(l.get("text", "") for l in lines[:40])  # ~500 words

        tickers = ", ".join(row.company_tickers or ["Unknown"])
        note_type = row.note_type.replace("_", " ").title()

        prompt = _TOPIC_SUGGESTION_PROMPT.format(
            note_type=note_type,
            tickers=tickers,
            transcript_excerpt=excerpt[:2000],
        )
        try:
            raw = self.llm.generate_response(prompt)
            import json
            topics = json.loads(raw)
            return [t for t in topics if isinstance(t, str)][:8]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Step 2: Extract topic fragments from transcript
    # ------------------------------------------------------------------

    def mark_complete(self, note_id: str, tenant_id: str) -> Optional[MeetingNote]:
        """
        Flip summary_status to COMPLETE. Used to unstick notes that landed in
        AWAITING_APPROVAL under the old delta-comparison flow.
        """
        row = self._fetch_row(note_id, tenant_id)
        if row is None:
            return None
        row.summary_status = SummaryStatus.COMPLETE.value
        row.updated_at = datetime.utcnow()
        self.db.commit()
        return self._row_to_domain(row)

    def _derive_topics_from_context(
        self,
        row: MeetingNoteORM,
    ) -> List[str]:
        """
        Derive topics when the user supplies none. Prioritises user's own notes
        (editor_plain_text); falls back to transcript excerpt if notes are empty.
        """
        import json

        lines = row.transcript_lines or []
        excerpt = " ".join(l.get("text", "") for l in lines[:50])[:3000]
        user_notes = (row.editor_plain_text or "").strip()[:3000] or "(user took no notes)"
        tickers = ", ".join(row.company_tickers or ["Unknown"])
        note_type = (row.note_type or "meeting").replace("_", " ").title()

        prompt = _TOPIC_DERIVATION_PROMPT.format(
            note_type=note_type,
            tickers=tickers,
            user_notes=user_notes,
            transcript_excerpt=excerpt,
        )
        try:
            raw = self.llm.generate_response(prompt)
            topics = json.loads(raw)
            return [t for t in topics if isinstance(t, str) and t.strip()][:6]
        except Exception:
            # Fallback: use the existing suggestion path (transcript-only)
            return self.suggest_topics(row.note_id, row.tenant_id)[:6]

    def extract_topic_fragments(
        self,
        note_id: str,
        tenant_id: str,
        user_topics: List[str],
    ) -> MeetingNote:
        """
        For each topic: run LLM extraction, build TopicFragment + DataFragment.
        Returns note with summary_status=COMPLETE.

        If user_topics is empty, derive topics from the user's own notes +
        transcript before extraction.

        NOTE: the former delta-vs-previous-meetings step has been removed.
        Comparing against prior meetings requires prior fragments for the same
        ticker, which is typically absent for the first meeting on any new
        ticker and produced a misleading "No significant changes found" state
        with no way to advance. The full comparison flow will come back in
        Plan 3 as the `compare_vs_previous` chat-agent tool (user-triggered).
        """
        import json

        row = self._fetch_row(note_id, tenant_id)
        row.summary_status = SummaryStatus.EXTRACTING.value
        self.db.commit()

        # If user provided no topics, auto-derive from their notes + transcript.
        if not user_topics:
            user_topics = self._derive_topics_from_context(row)

        lines = row.transcript_lines or []
        transcript_text = self._format_transcript_for_llm(lines)

        topic_fragments: List[Dict] = []
        all_fragment_ids: List[str] = list(row.fragment_ids or [])

        for topic in user_topics:
            prompt = _TOPIC_EXTRACTION_PROMPT.format(
                topic=topic,
                transcript_text=transcript_text[:6000],
            )
            try:
                raw = self.llm.generate_response(prompt)
                extracted = json.loads(raw)
            except Exception:
                extracted = {
                    "topic": topic,
                    "topic_summary": f"Could not extract details for topic: {topic}",
                    "supporting_sentences": [],
                    "overall_tone": "neutral",
                    "direction": "stable",
                    "key_numbers": [],
                    "speakers_involved": [],
                }

            # Build DataFragment (one per topic)
            fragment = self._build_meeting_fragment(
                row, topic, extracted
            )
            fragment_id = self._save_fragment(fragment)
            extracted["fragment_id"] = fragment_id
            all_fragment_ids.append(fragment_id)
            topic_fragments.append(extracted)

        # Build a short ai_narrative from the extracted topics so CompleteStep
        # has something to show.
        topic_names = [tf.get("topic", "") for tf in topic_fragments if tf.get("topic")]
        if topic_names:
            narrative = (
                f"Extracted {len(topic_names)} topic "
                f"{'fragment' if len(topic_names) == 1 else 'fragments'}: "
                + ", ".join(topic_names[:6])
                + (f", and {len(topic_names) - 6} more" if len(topic_names) > 6 else "")
                + "."
            )
        else:
            narrative = "No topic fragments were extracted from this meeting."

        # Persist — go straight to COMPLETE. Delta-vs-previous comparison is
        # intentionally skipped (will return as a chat-agent tool in Plan 3).
        summary = row.ai_summary or {}
        summary["user_topics"] = user_topics
        summary["topic_fragments"] = topic_fragments
        summary["delta_cards"] = []
        summary["ai_narrative"] = narrative
        row.ai_summary = summary
        row.fragment_ids = all_fragment_ids
        row.summary_status = SummaryStatus.COMPLETE.value
        row.updated_at = datetime.utcnow()
        self.db.commit()
        return self._row_to_domain(row)

    # ------------------------------------------------------------------
    # Step 3: Delta card approval / edit / dismiss
    # ------------------------------------------------------------------

    def process_delta(
        self,
        note_id: str,
        tenant_id: str,
        delta_id: str,
        action: str,              # "approve" | "edit" | "dismiss"
        edited_text: Optional[str] = None,
    ) -> MeetingNote:
        """
        approve -> creates MEETING_DELTA DataFragment.
        edit    -> uses edited_text, creates MEETING_DELTA DataFragment.
        dismiss -> marks as DISMISSED, no fragment created.
        """
        row = self._fetch_row(note_id, tenant_id)
        summary = row.ai_summary or {}
        delta_cards = summary.get("delta_cards", [])

        for card_dict in delta_cards:
            if card_dict.get("delta_id") != delta_id:
                continue

            card = DeltaCard(**card_dict)
            if action == "dismiss":
                card.status = "DISMISSED"
            else:
                final_text = edited_text if (action == "edit" and edited_text) else card.current_statement
                card.edited_text = final_text if action == "edit" else None
                card.status = "APPROVED" if action == "approve" else "EDITED"

                # Persist as MEETING_DELTA DataFragment
                delta_fragment = self._build_delta_fragment(row, card, final_text)
                frag_id = self._save_fragment(delta_fragment)
                card.approved_fragment_id = frag_id
                fragment_ids = list(row.fragment_ids or [])
                fragment_ids.append(frag_id)
                row.fragment_ids = fragment_ids

            # Update card in summary
            for i, c in enumerate(delta_cards):
                if c.get("delta_id") == delta_id:
                    delta_cards[i] = card.model_dump()
                    break
            break

        summary["delta_cards"] = delta_cards

        # Auto-advance to COMPLETE when all cards are resolved
        all_resolved = all(
            c.get("status") in ("APPROVED", "EDITED", "DISMISSED")
            for c in delta_cards
        )
        if all_resolved and delta_cards:
            row.summary_status = SummaryStatus.COMPLETE.value
            # Generate note enhancements on completion
            summary["note_enhancements"] = self._generate_note_enhancements(row)

        row.ai_summary = summary
        row.updated_at = datetime.utcnow()
        self.db.commit()
        return self._row_to_domain(row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_delta_comparison(
        self,
        tenant_id: str,
        company_tickers: List[str],
        note_id: str,
        topic_fragments: List[Dict],
        current_source: str,
    ) -> List[DeltaCard]:
        """For each topic, pull previous MEETING_NOTE fragments and compare."""
        import json

        delta_cards: List[DeltaCard] = []

        for tf in topic_fragments:
            topic = tf.get("topic", "")
            current_summary = tf.get("topic_summary", "")

            # Fetch previous fragments for this topic + company
            previous = self._fetch_previous_fragments(tenant_id, company_tickers, topic, exclude_note_id=note_id)
            if not previous:
                continue

            for prev_frag in previous[:2]:   # compare against last 2 occurrences
                prev_source = prev_frag.get("source", "Previous meeting")
                prev_text = prev_frag.get("content", {}).get("extracted_metrics", {}).get("topic_summary", "")

                if not prev_text:
                    continue

                prompt = _DELTA_COMPARISON_PROMPT.format(
                    topic=topic,
                    previous_source=prev_source,
                    previous_text=prev_text,
                    current_text=current_summary,
                )
                try:
                    raw = self.llm.generate_response(prompt)
                    changes = json.loads(raw)
                    if not isinstance(changes, list):
                        changes = []
                except Exception:
                    changes = []

                for change in changes:
                    delta_cards.append(DeltaCard(
                        topic=topic,
                        previous_statement=change.get("previous_statement", prev_text[:300]),
                        previous_source=prev_source,
                        current_statement=change.get("current_statement", current_summary[:300]),
                        change_type=change.get("change_type", "SIGNIFICANT"),
                        significance=change.get("significance", "MEDIUM"),
                    ))

        return delta_cards

    def _fetch_previous_fragments(
        self,
        tenant_id: str,
        tickers: List[str],
        topic: str,
        exclude_note_id: str,
    ) -> List[Dict]:
        """Return last 2 MEETING_NOTE fragments for the same company + topic keyword."""
        from backend.app.models.orm.fragment_orm import FragmentORM
        results = (
            self.db.query(FragmentORM)
            .filter(
                FragmentORM.tenant_id == tenant_id,
                FragmentORM.source_type == SourceType.MEETING_NOTE.value,
            )
            .order_by(FragmentORM.created_at.desc())
            .limit(50)
            .all()
        )
        matched = []
        topic_lower = topic.lower()
        for r in results:
            # Skip fragments from the current note
            if exclude_note_id in (r.source or ""):
                continue
            metrics = (r.content or {}).get("extracted_metrics", {})
            if topic_lower in (metrics.get("topic", "")).lower():
                matched.append({
                    "source": r.source,
                    "content": r.content,
                    "created_at": str(r.created_at),
                })
            if len(matched) >= 2:
                break
        return matched

    def _build_meeting_fragment(self, row: MeetingNoteORM, topic: str, extracted: Dict) -> DataFragment:
        """Build a MEETING_NOTE DataFragment from the LLM extraction output."""
        sentences = extracted.get("supporting_sentences", [])
        key_numbers = extracted.get("key_numbers", [])
        return DataFragment(
            tenant_id=row.tenant_id,
            tenant_tier=TenantTier.PRIVATE,
            source_type=SourceType.MEETING_NOTE,
            source=f"{row.note_id}::{row.title}",
            exact_location=f"Meeting transcript — {row.meeting_date or 'date unknown'}",
            reason_for_extraction=f"User-requested topic: {topic}",
            content={
                "raw_text": extracted.get("topic_summary", ""),
                "extracted_metrics": {
                    "topic": topic,
                    "topic_summary": extracted.get("topic_summary", ""),
                    "supporting_sentences": sentences,
                    "overall_tone": extracted.get("overall_tone", "neutral"),
                    "direction": extracted.get("direction", "stable"),
                    "key_numbers": key_numbers,
                    "speakers_involved": extracted.get("speakers_involved", []),
                    "company_tickers": row.company_tickers or [],
                    "note_type": row.note_type,
                    "sentence_count": len(sentences),
                },
            },
            lineage=[row.note_id],
        )

    def _build_delta_fragment(self, row: MeetingNoteORM, card: DeltaCard, final_text: str) -> DataFragment:
        """Build a MEETING_DELTA DataFragment from an approved DeltaCard."""
        return DataFragment(
            tenant_id=row.tenant_id,
            tenant_tier=TenantTier.PRIVATE,
            source_type=SourceType.MEETING_DELTA,
            source=f"{row.note_id}::{row.title}",
            exact_location=f"Delta vs. {card.previous_source}",
            reason_for_extraction=f"User-approved change on topic: {card.topic}",
            content={
                "raw_text": final_text,
                "extracted_metrics": {
                    "topic": card.topic,
                    "change_type": card.change_type,
                    "significance": card.significance,
                    "previous_statement": card.previous_statement,
                    "previous_source": card.previous_source,
                    "current_statement": final_text,
                    "company_tickers": row.company_tickers or [],
                    "note_type": row.note_type,
                },
            },
            lineage=[row.note_id, card.delta_id],
        )

    def _save_fragment(self, fragment: DataFragment) -> str:
        """Persist DataFragment via db_repo (dedup-aware)."""
        fingerprint = hashlib.sha256(
            f"{fragment.tenant_id}:{fragment.source}:{fragment.exact_location}".encode()
        ).hexdigest()

        from backend.app.models.orm.fragment_orm import FragmentORM
        existing = (
            self.db.query(FragmentORM)
            .filter(FragmentORM.content_fingerprint == fingerprint)
            .first()
        )
        if existing:
            return existing.fragment_id

        orm = FragmentORM(
            fragment_id=str(fragment.fragment_id),
            tenant_id=fragment.tenant_id,
            tenant_tier=fragment.tenant_tier,
            source_type=fragment.source_type,
            source=fragment.source,
            exact_location=fragment.exact_location,
            reason_for_extraction=fragment.reason_for_extraction,
            content=fragment.content,
            content_fingerprint=fingerprint,
            lineage=fragment.lineage,
            created_at=fragment.created_at,
        )
        self.db.add(orm)
        self.db.commit()
        return str(fragment.fragment_id)

    def _generate_note_enhancements(self, row: MeetingNoteORM) -> List[str]:
        """Suggest 2-4 additions to the user's written notes based on what's in the transcript."""
        import json

        user_notes = row.editor_plain_text or ""
        if not user_notes.strip():
            return []

        summary = row.ai_summary or {}
        topic_fragments = summary.get("topic_fragments", [])
        all_summaries = " ".join(tf.get("topic_summary", "") for tf in topic_fragments)

        prompt = _NOTE_ENHANCEMENT_PROMPT.format(
            user_notes=user_notes[:1500],
            uncovered_points=all_summaries[:2000],
        )
        try:
            raw = self.llm.generate_response(prompt)
            suggestions = json.loads(raw)
            return [s for s in suggestions if isinstance(s, str)][:4]
        except Exception:
            return []

    @staticmethod
    def _format_transcript_for_llm(lines: List[Dict]) -> str:
        parts = []
        for l in lines:
            speaker = l.get("speaker_name") or l.get("speaker_label", "?")
            parts.append(f"[{l.get('line_id', '')}] {l.get('timestamp', '')} {speaker}: {l.get('text', '')}")
        return "\n".join(parts)

    def _fetch_row(self, note_id: str, tenant_id: str) -> MeetingNoteORM:
        row = (
            self.db.query(MeetingNoteORM)
            .filter(MeetingNoteORM.note_id == note_id, MeetingNoteORM.tenant_id == tenant_id)
            .first()
        )
        if not row:
            raise ValueError(f"Note {note_id} not found for tenant {tenant_id}")
        return row

    @staticmethod
    def _row_to_domain(row: MeetingNoteORM) -> MeetingNote:
        from backend.app.services.notes_service import NotesService
        return NotesService._to_domain(row)
