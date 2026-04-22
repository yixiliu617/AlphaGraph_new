# Meeting Intelligence — Plan 1: Variant Infrastructure + Bilingual Editor Auto-Insert

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the A/B variant infrastructure and the editor auto-insert behaviour (three bilingual sections) end-to-end. After this plan, any user — on either an A or B note — can record a short meeting, click "Save Both", and see their own notes + raw transcript + polished transcript appear as three editable sections in the main editor. This also fixes the current A-variant bug where the polished transcript was generated but had nowhere to display. A/B only affects the sidebar (wizard vs. Meeting Intelligence Panel), which lands in Plans 2 + 3.

**Architecture:** Add a `ux_variant` column that gates every new code path. Change the Gemini polish call to emit structured JSON with per-segment `text_original` + `text_english`, persist the structured form, and extend the WebSocket message so the client can build a TipTap bilingual table without a round-trip. Frontend gets a shared `editorSectionBuilder` helper (one file) that both constructs the TipTap JSON for each section and handles idempotent insert-or-replace based on heading `sectionId` attrs. `RecordingPanel.onComplete` grows a polished-payload parameter; the container wires Save Both (B only) to call the builder.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite dev DB), Pydantic domain models, Gemini 2.5 Flash via `requests`, Next.js 15 + React 19 + TipTap 2 (`@tiptap/starter-kit`, `@tiptap/extension-table` already installed), Zustand, Tailwind v4, `lucide-react`. Backend uses pytest with an in-memory SQLite fixture (`sqlite_session` in `backend/tests/integration/conftest.py`). Frontend has no test framework — verify via `npx tsc --noEmit` + manual smoke test.

**Out of scope for Plan 1** (comes later in Plan 2 / Plan 3):
- `MeetingIntelligencePanel` sidebar — the right-hand side still shows the existing `PostMeetingWizard` or `NoteSearchPanel`.
- Chat agent, analysis modules, `chat_messages` column, `analysis_jobs` column.
- Audio scrubber seeking from transcript timestamps inside the editor (the existing timestamp-click plugin handles anchor tags; we rely on that).

---

## File Structure

**Backend — modify:**
- `backend/app/models/orm/note_orm.py` — add `ux_variant: Column(String, default="A")`.
- `backend/app/models/domain/meeting_note.py` — add `ux_variant: Literal["A", "B"] = "A"`.
- `backend/app/services/notes_service.py` — `create_note` accepts `ux_variant`; `_to_orm`/`_to_domain` handle the field.
- `backend/app/api/routers/v1/notes.py` — `CreateNoteRequest` adds `ux_variant`; `create_note` passes it through; `_run_live_v2_session` sends structured polished payload over WebSocket.
- `backend/app/services/live_transcription.py` — `gemini_batch_transcribe` emits structured JSON (parses Gemini's `responseMimeType: application/json` output) and returns `{language, is_bilingual, key_topics, segments, text, input_tokens, output_tokens}`.

**Backend — create:**
- `backend/tests/integration/test_notes_ux_variant.py` — pytest integration tests for the new field.
- `backend/tests/unit/test_live_transcription_parse.py` — unit tests for the structured-polish JSON parser.

**Backend — script (one-shot migration):**
- Not a committed file; just a shell command run against `alphagraph.db`. Step included in Task 1.

**Frontend — modify:**
- `frontend/src/lib/api/notesClient.ts` — `NoteStub.ux_variant` field; `create()` accepts optional `ux_variant`; new `PolishedSegment` type; `TranscriptLine` type stays the same (already carries `translation` + `language`).
- `frontend/src/components/domain/notes/NoteCreationModal.tsx` — variant picker UI; propagates `ux_variant` through `onCreate`.
- `frontend/src/app/(dashboard)/notes/NotesContainer.tsx` — `handleCreate` payload includes `ux_variant`.
- `frontend/src/app/(dashboard)/notes/NotesView.tsx` — `NoteRow` renders an `[A]` / `[B]` badge; `Props.onCreate` payload typing gains `ux_variant`.
- `frontend/src/components/domain/notes/RecordingPanel.tsx` — capture polished `segments`/`is_bilingual`/`key_topics` from WebSocket; `onComplete` signature extended.
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx` — on Save Both for B notes, call `editorSectionBuilder` helpers and patch `editor_content` via existing auto-save.
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — `Props.onRecordingComplete` signature match.
- `frontend/src/components/domain/notes/RichTextEditor.tsx` — use extended Heading node that preserves a `sectionId` HTML attribute on `<h2>` tags.

**Frontend — create:**
- `frontend/src/components/domain/notes/editorSectionBuilder.ts` — all builder + insert logic in one file. Exports: `buildBilingualTableJson`, `buildRawTranscriptSectionNodes`, `buildPolishedTranscriptSectionNodes`, `buildUserNotesSectionNodes`, `insertOrReplaceSection`.
- `frontend/src/components/domain/notes/sectionHeadingExtension.ts` — tiny TipTap extension that wraps `@tiptap/extension-heading` to persist a `sectionId` attr.

Total new files: 3 (one TS helper, one TipTap extension, two pytest files). All other changes are edits to existing files.

---

## Task 1: Backend — `ux_variant` column, domain, service, endpoint, migration

**Files:**
- Modify: `backend/app/models/orm/note_orm.py`
- Modify: `backend/app/models/domain/meeting_note.py`
- Modify: `backend/app/services/notes_service.py`
- Modify: `backend/app/api/routers/v1/notes.py`
- Create: `backend/tests/integration/test_notes_ux_variant.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_notes_ux_variant.py`:

```python
"""
Integration tests for the ux_variant column on MeetingNote.
Verifies default value, explicit set, round-trip through ORM/domain.
"""

import pytest

from backend.app.services.notes_service import NotesService
# Ensure note ORM is registered for create_all in the sqlite_session fixture
import backend.app.models.orm.note_orm  # noqa: F401


TENANT = "Institutional_L1"


def test_create_note_defaults_to_variant_a(sqlite_session):
    svc = NotesService(sqlite_session)
    note = svc.create_note(
        tenant_id=TENANT,
        title="Default variant test",
        note_type="internal",
        company_tickers=["NVDA"],
    )
    assert note.ux_variant == "A"


def test_create_note_explicit_variant_b(sqlite_session):
    svc = NotesService(sqlite_session)
    note = svc.create_note(
        tenant_id=TENANT,
        title="B variant test",
        note_type="internal",
        company_tickers=["NVDA"],
        ux_variant="B",
    )
    assert note.ux_variant == "B"


def test_variant_round_trips_via_get(sqlite_session):
    svc = NotesService(sqlite_session)
    created = svc.create_note(
        tenant_id=TENANT,
        title="Round trip",
        note_type="internal",
        company_tickers=["NVDA"],
        ux_variant="B",
    )
    fetched = svc.get_note(created.note_id, TENANT)
    assert fetched is not None
    assert fetched.ux_variant == "B"
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from repo root):

```bash
cd backend && python -m pytest tests/integration/test_notes_ux_variant.py -v
```

Expected: all three fail. The first two with `TypeError: ... got an unexpected keyword argument 'ux_variant'` (or AttributeError on `note.ux_variant`). The third likewise.

- [ ] **Step 3: Add `ux_variant` to ORM**

Edit `backend/app/models/orm/note_orm.py`. Find this block:

```python
    # AI summary flow state
    summary_status = Column(String, default="none")
```

Add the column **before** it:

```python
    # A/B experiment variant — "A" = classic (wizard in sidebar),
    # "B" = new (transcripts in editor + chat). Default A so existing code paths
    # are unchanged for every pre-existing and newly-created note unless the
    # caller opts into B.
    ux_variant = Column(String, default="A", nullable=False)

    # AI summary flow state
    summary_status = Column(String, default="none")
```

- [ ] **Step 4: Add `ux_variant` to domain model**

Edit `backend/app/models/domain/meeting_note.py`. At the top, add the `Literal` import if missing:

```python
from typing import Any, Dict, List, Literal, Optional
```

Find the `MeetingNote` class and add the field right after `editor_plain_text`:

```python
    editor_plain_text: str = ""

    # A/B experiment variant — see ORM comment for full context.
    ux_variant: Literal["A", "B"] = "A"
```

- [ ] **Step 5: Update service to carry `ux_variant`**

Edit `backend/app/services/notes_service.py`.

Find `create_note` signature and extend it:

```python
    def create_note(
        self,
        tenant_id: str,
        title: str,
        note_type: str,
        company_tickers: List[str],
        meeting_date: Optional[str] = None,
        ux_variant: str = "A",
    ) -> MeetingNote:
        note = MeetingNote(
            tenant_id=tenant_id,
            title=title,
            note_type=note_type,
            company_tickers=company_tickers,
            meeting_date=meeting_date,
            editor_content={"type": "doc", "content": []},
            ux_variant=ux_variant,  # type: ignore[arg-type]
        )
        orm = self._to_orm(note)
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return self._to_domain(orm)
```

Find `_to_orm` and add `ux_variant=note.ux_variant,` after the existing `editor_plain_text=note.editor_plain_text,` line (keeping alphabetical-ish order irrelevant — append near other simple scalars):

```python
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
            ux_variant=note.ux_variant,
            recording_path=note.recording_path,
            recording_mode=note.recording_mode,
            duration_seconds=note.duration_seconds,
            transcript_lines=[l.model_dump() for l in note.transcript_lines],
            polished_transcript=note.polished_transcript,
            polished_transcript_language=note.polished_transcript_language,
            polished_transcript_meta=note.polished_transcript_meta,
            summary_status=note.summary_status,
            ai_summary=note.ai_summary.model_dump() if note.ai_summary else None,
            fragment_ids=note.fragment_ids,
        )
```

Find `_to_domain` and add `ux_variant=row.ux_variant or "A",` in the same position:

```python
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
            ux_variant=(row.ux_variant or "A"),  # type: ignore[arg-type]
            recording_path=row.recording_path,
            recording_mode=row.recording_mode,
            duration_seconds=row.duration_seconds,
            transcript_lines=[
                TranscriptLine(**l) for l in (row.transcript_lines or [])
            ],
            polished_transcript=row.polished_transcript,
            polished_transcript_language=row.polished_transcript_language,
            polished_transcript_meta=row.polished_transcript_meta,
            summary_status=SummaryStatus(row.summary_status or "none"),
            ai_summary=AISummary(**row.ai_summary) if row.ai_summary else None,
            fragment_ids=row.fragment_ids or [],
        )
```

- [ ] **Step 6: Expose `ux_variant` through the REST endpoint**

Edit `backend/app/api/routers/v1/notes.py`. Find `CreateNoteRequest`:

```python
class CreateNoteRequest(BaseModel):
    title: str
    note_type: str
    company_tickers: List[str]
    meeting_date: Optional[str] = None
    ux_variant: str = "A"
```

Find `create_note` and pass the field through:

```python
@router.post("", response_model=APIResponse)
def create_note(request: CreateNoteRequest, db: Session = Depends(get_db_session)):
    svc = NotesService(db)
    note = svc.create_note(
        tenant_id=TENANT_ID,
        title=request.title,
        note_type=request.note_type,
        company_tickers=request.company_tickers,
        meeting_date=request.meeting_date,
        ux_variant=request.ux_variant,
    )
    return APIResponse(success=True, data=note.model_dump())
```

- [ ] **Step 7: Run tests to verify they pass**

Run (from repo root):

```bash
cd backend && python -m pytest tests/integration/test_notes_ux_variant.py -v
```

Expected: all three tests PASS.

- [ ] **Step 8: Run the dev-DB migration**

Add the column to the live SQLite DB (same pattern we used for `polished_transcript`):

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -c "
import sqlite3
conn = sqlite3.connect('alphagraph.db')
cur = conn.cursor()
existing = {row[1] for row in cur.execute('PRAGMA table_info(meeting_notes)').fetchall()}
if 'ux_variant' not in existing:
    cur.execute(\"ALTER TABLE meeting_notes ADD COLUMN ux_variant VARCHAR NOT NULL DEFAULT 'A'\")
    print('added ux_variant')
else:
    print('ux_variant already exists')
conn.commit()
conn.close()
"
```

Expected output: `added ux_variant` (or `already exists` on re-run).

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/orm/note_orm.py backend/app/models/domain/meeting_note.py backend/app/services/notes_service.py backend/app/api/routers/v1/notes.py backend/tests/integration/test_notes_ux_variant.py alphagraph.db
git commit -m "feat(notes): add ux_variant column for A/B layout experiment"
```

---

## Task 2: Backend — Structured polish JSON + WebSocket extension

**Files:**
- Modify: `backend/app/services/live_transcription.py`
- Modify: `backend/app/api/routers/v1/notes.py`
- Create: `backend/tests/unit/test_live_transcription_parse.py`

The current `gemini_batch_transcribe` returns `{text, input_tokens, output_tokens, language}` with free-form markdown in `text`. We change the prompt to ask Gemini for JSON matching a specific schema (via `responseMimeType: application/json`), parse it, and return `{language, is_bilingual, key_topics, segments, text, input_tokens, output_tokens}` where `text` is a flattened markdown form built from `segments` (for backward-compat export). The WebSocket message grows three new fields so B clients have everything they need in one round-trip.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_live_transcription_parse.py`:

```python
"""
Unit tests for the structured-output parser in live_transcription.
We don't hit the Gemini API; we only verify our handling of the JSON shape.
"""

import json
import pytest

from backend.app.services.live_transcription import (
    _parse_polish_response,
    _flatten_segments_to_markdown,
)


SAMPLE_JA_JSON = {
    "language": "ja",
    "is_bilingual": True,
    "key_topics": ["revenue", "ARM"],
    "segments": [
        {
            "timestamp": "00:15",
            "speaker": "Tanaka (CFO)",
            "text_original": "売上高は前年比20%増となりました。",
            "text_english": "Revenue grew 20% year-over-year.",
        },
        {
            "timestamp": "00:32",
            "speaker": "Tanaka (CFO)",
            "text_original": "ARMとの提携は順調です。",
            "text_english": "Our ARM partnership is progressing well.",
        },
    ],
}


def test_parse_polish_response_happy_path():
    parsed = _parse_polish_response(json.dumps(SAMPLE_JA_JSON))
    assert parsed["language"] == "ja"
    assert parsed["is_bilingual"] is True
    assert parsed["key_topics"] == ["revenue", "ARM"]
    assert len(parsed["segments"]) == 2
    assert parsed["segments"][0]["text_english"] == "Revenue grew 20% year-over-year."


def test_parse_polish_response_falls_back_for_non_json():
    """If Gemini returns non-JSON (prompt drift), we degrade gracefully."""
    parsed = _parse_polish_response("This is just plain markdown, not JSON.")
    assert parsed["language"] == ""
    assert parsed["is_bilingual"] is False
    assert parsed["key_topics"] == []
    assert parsed["segments"] == []
    # Raw text preserved so the user still sees *something*.
    assert "plain markdown" in parsed["text_markdown_fallback"]


def test_flatten_segments_bilingual():
    md = _flatten_segments_to_markdown(SAMPLE_JA_JSON["segments"], is_bilingual=True)
    # Bilingual form renders as a markdown table with 3 columns.
    assert "| Time" in md
    assert "| 売上高は前年比20%増となりました。" in md
    assert "| Revenue grew 20% year-over-year. |" in md
    assert "00:15" in md and "00:32" in md


def test_flatten_segments_monolingual():
    segments = [
        {"timestamp": "00:10", "speaker": "Alice", "text_original": "Hello.", "text_english": "Hello."},
    ]
    md = _flatten_segments_to_markdown(segments, is_bilingual=False)
    # Monolingual form uses 2-column table; English column suppressed.
    assert "| Time" in md
    assert "| Text" in md
    assert "English" not in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && python -m pytest tests/unit/test_live_transcription_parse.py -v
```

Expected: all four tests fail with `ImportError` — `_parse_polish_response` and `_flatten_segments_to_markdown` don't exist yet.

- [ ] **Step 3: Rewrite the polish prompt + add parsers**

Edit `backend/app/services/live_transcription.py`. Replace the entire `gemini_batch_transcribe` function with:

```python
def gemini_batch_transcribe(
    audio_path: str,
    language: str = "zh",
    note_id: str = "",
) -> dict:
    """
    Run Gemini V2-quality batch transcription on the full audio file.

    Returns a structured dict:
      {
        "language": str,           # detected language code
        "is_bilingual": bool,      # True for zh/ja/ko source (English translation provided)
        "key_topics": list[str],
        "segments": [              # one entry per spoken segment
          {"timestamp": "MM:SS", "speaker": str,
           "text_original": str, "text_english": str},
          ...
        ],
        "text": str,               # flattened markdown form (for export/backup)
        "input_tokens": int,
        "output_tokens": int,
        "error": str (optional),
      }
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "error": "GEMINI_API_KEY not set",
            "language": language,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    vocab_context = load_vocabulary(language)

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    ext = Path(audio_path).suffix.lower()
    mime_types = {".opus": "audio/ogg", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
    mime = mime_types.get(ext, "audio/ogg")

    lang_names = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}
    lang_name = lang_names.get(language, "Chinese")

    prompt = f"""{vocab_context}
Transcribe this financial meeting audio. Primary language: {lang_name} with English code-switching.

Return ONLY valid JSON matching this exact schema:
{{
  "language": "{language}",
  "is_bilingual": true,
  "key_topics": ["topic1", "topic2", ...],
  "segments": [
    {{
      "timestamp": "MM:SS",
      "speaker": "speaker name or role (e.g. 'Tanaka (CFO)')",
      "text_original": "exact transcription in the meeting's primary language",
      "text_english": "English translation of this segment"
    }}
  ]
}}

Rules:
1. Timestamps in MM:SS format relative to the start of the audio.
2. Provide `text_english` for every segment. For English-only meetings, set `text_english` equal to `text_original`.
3. For English-only meetings, set `is_bilingual` to false.
4. NEVER repeat a segment. If audio is unclear, emit a single segment with text_original="[audio unclear]".
5. Preserve financial terminology and proper nouns exactly as spoken."""

    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": audio_b64}},
            ]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 65536,
                "responseMimeType": "application/json",
            },
        },
        timeout=900,
    )

    if resp.status_code != 200:
        return {
            "error": f"Gemini API error: {resp.status_code}",
            "language": language,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    result = resp.json()
    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})

    parsed = _parse_polish_response(raw_text)
    # Fill in the fallback markdown if parsing failed so downstream still has *something* to show.
    text_md = _flatten_segments_to_markdown(parsed["segments"], parsed["is_bilingual"]) \
        if parsed["segments"] else parsed.get("text_markdown_fallback", "")

    return {
        "language": parsed["language"] or language,
        "is_bilingual": parsed["is_bilingual"],
        "key_topics": parsed["key_topics"],
        "segments": parsed["segments"],
        "text": text_md,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }


def _parse_polish_response(raw_text: str) -> dict:
    """
    Parse Gemini's structured-output response. Returns a dict with keys:
    `language`, `is_bilingual`, `key_topics`, `segments`, and optionally
    `text_markdown_fallback` when we couldn't parse JSON.
    """
    import json as _json
    try:
        data = _json.loads(raw_text)
        segments = [
            {
                "timestamp": str(s.get("timestamp", "")),
                "speaker": str(s.get("speaker", "")),
                "text_original": str(s.get("text_original", "")),
                "text_english": str(s.get("text_english", "")),
            }
            for s in (data.get("segments") or [])
            if isinstance(s, dict)
        ]
        # Anti-repetition pass on the assembled segments (kept here rather than
        # in the prompt because Gemini sometimes produces duplicates anyway).
        deduped: list[dict] = []
        for seg in segments:
            if deduped and seg["text_original"] == deduped[-1]["text_original"]:
                continue
            deduped.append(seg)

        return {
            "language": str(data.get("language", "")),
            "is_bilingual": bool(data.get("is_bilingual", False)),
            "key_topics": [str(t) for t in (data.get("key_topics") or []) if t],
            "segments": deduped,
        }
    except (ValueError, KeyError, TypeError):
        return {
            "language": "",
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "text_markdown_fallback": raw_text,
        }


def _flatten_segments_to_markdown(segments: list, is_bilingual: bool) -> str:
    """
    Render segments as a markdown table. Two columns for monolingual
    (Time | Text) or three for bilingual (Time | Original | English).
    Used for export / backup; the frontend builds its own TipTap table
    directly from the structured segments, not this markdown.
    """
    if not segments:
        return ""

    if is_bilingual:
        lines = ["| Time | 原文 | English |", "|------|------|---------|"]
        for s in segments:
            ts = s.get("timestamp", "")
            orig = (s.get("text_original", "") or "").replace("|", "\\|").replace("\n", " ")
            eng = (s.get("text_english", "") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {ts} | {orig} | {eng} |")
        return "\n".join(lines)
    else:
        lines = ["| Time | Text |", "|------|------|"]
        for s in segments:
            ts = s.get("timestamp", "")
            txt = (s.get("text_original", "") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {ts} | {txt} |")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd backend && python -m pytest tests/unit/test_live_transcription_parse.py -v
```

Expected: all four tests PASS.

- [ ] **Step 5: Extend the WebSocket polish message + persistence**

Edit `backend/app/api/routers/v1/notes.py`. Find the block inside `_run_live_v2_session` where we currently send the `polished_transcript` WebSocket message (just after `svc.save_polished_transcript(...)` persistence):

Replace the `await websocket.send_json({ ...polished_transcript... })` call with the extended message, and enrich the persisted `meta`:

```python
        if result.get("error"):
            await websocket.send_json({
                "type": "error", "message": f"Gemini error: {result['error']}",
            })
        else:
            # Persist polished transcript + structured segments before notifying
            # the client, so it's durable even if the client disconnects.
            from backend.app.db.session import SessionLocal
            db2 = SessionLocal()
            try:
                svc = NotesService(db2)
                svc.save_polished_transcript(
                    note_id=note_id,
                    tenant_id=TENANT_ID,
                    markdown=result["text"],
                    language=result.get("language", final_lang),
                    meta={
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "model": "gemini-2.5-flash",
                        "ran_at": datetime.utcnow().isoformat(),
                        "is_bilingual": result.get("is_bilingual", False),
                        "key_topics": result.get("key_topics", []),
                        "segments": result.get("segments", []),
                    },
                )
            finally:
                db2.close()

            await websocket.send_json({
                "type": "polished_transcript",
                "text": result["text"],
                "language": result.get("language", final_lang),
                "is_bilingual": result.get("is_bilingual", False),
                "key_topics": result.get("key_topics", []),
                "segments": result.get("segments", []),
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
            })
            await websocket.send_json({
                "type": "status", "status": "complete",
                "message": "Polished transcript ready.",
            })
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/live_transcription.py backend/app/api/routers/v1/notes.py backend/tests/unit/test_live_transcription_parse.py
git commit -m "feat(notes): structured polish output with bilingual segments"
```

---

## Task 3: Frontend — `NoteStub` type, NoteCreationModal picker, library badge

**Files:**
- Modify: `frontend/src/lib/api/notesClient.ts`
- Modify: `frontend/src/components/domain/notes/NoteCreationModal.tsx`
- Modify: `frontend/src/app/(dashboard)/notes/NotesContainer.tsx`
- Modify: `frontend/src/app/(dashboard)/notes/NotesView.tsx`

- [ ] **Step 1: Extend `NoteStub`, `create`, and add `PolishedSegment` type**

Edit `frontend/src/lib/api/notesClient.ts`.

Find the `NoteStub` interface and add `ux_variant`:

```typescript
export interface NoteStub {
  note_id: string;
  tenant_id: string;
  title: string;
  note_type: string;
  company_tickers: string[];
  meeting_date: string | null;
  created_at: string;
  updated_at: string;
  editor_content: Record<string, unknown>;
  editor_plain_text: string;
  ux_variant: "A" | "B";
  recording_path: string | null;
  recording_mode: string | null;
  duration_seconds: number | null;
  transcript_lines: TranscriptLine[];
  summary_status: string;
  ai_summary: AISummary | null;
  fragment_ids: string[];
}
```

Immediately below `TranscriptLine`, add `PolishedSegment`:

```typescript
export interface PolishedSegment {
  timestamp: string;      // "MM:SS"
  speaker: string;
  text_original: string;
  text_english: string;
}
```

Find `create` and extend its payload type:

```typescript
  create: (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
    ux_variant?: "A" | "B";
  }) => apiRequest<AR<NoteStub>>(BASE, "POST", payload),
```

- [ ] **Step 2: Add variant picker to `NoteCreationModal`**

Edit `frontend/src/components/domain/notes/NoteCreationModal.tsx`.

Extend the `Props.onCreate` payload type:

```typescript
interface Props {
  onClose: () => void;
  onCreate: (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
    ux_variant: "A" | "B";
  }) => void;
}
```

Add state for the variant at the top of the component, right next to the other `useState` calls:

```typescript
  const [uxVariant, setUxVariant] = useState<"A" | "B">("A");
```

Inside the form (after the "Meeting date" block, before the footer), add a new block:

```typescript
          {/* A/B layout variant */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
              Layout <span className="text-slate-400 font-normal normal-case">(experiment)</span>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setUxVariant("A")}
                className={`px-3 py-2.5 text-xs font-medium rounded-md border text-left transition-colors ${
                  uxVariant === "A"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                }`}
              >
                <span className="font-mono mr-1">A</span> Classic
                <span className="block text-[10px] opacity-70 mt-0.5 normal-case">Wizard in sidebar</span>
              </button>
              <button
                type="button"
                onClick={() => setUxVariant("B")}
                className={`px-3 py-2.5 text-xs font-medium rounded-md border text-left transition-colors ${
                  uxVariant === "B"
                    ? "border-indigo-600 bg-indigo-600 text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                }`}
              >
                <span className="font-mono mr-1">B</span> New
                <span className="block text-[10px] opacity-70 mt-0.5 normal-case">Transcripts in editor + chat</span>
              </button>
            </div>
          </div>
```

Update `handleSubmit` to include `ux_variant` in the payload:

```typescript
    await onCreate({
      title: title.trim(),
      note_type: effectiveType,
      company_tickers: companies,
      meeting_date: meetingDate || undefined,
      ux_variant: uxVariant,
    });
```

- [ ] **Step 3: Propagate through `NotesContainer`**

Edit `frontend/src/app/(dashboard)/notes/NotesContainer.tsx`. Extend the `handleCreate` signature and pass through:

```typescript
  const handleCreate = async (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
    ux_variant: "A" | "B";
  }) => {
    const res = await notesClient.create(payload);
    if (res.success && res.data) {
      addNote(res.data);
      setShowCreateModal(false);
      router.push(`/notes/${res.data.note_id}`);
    }
  };
```

- [ ] **Step 4: Propagate type through `NotesView.Props`**

Edit `frontend/src/app/(dashboard)/notes/NotesView.tsx`. Find `Props.onCreate` and extend:

```typescript
  onCreate: (payload: {
    title: string; note_type: string; company_tickers: string[]; meeting_date?: string;
    ux_variant: "A" | "B";
  }) => void;
```

- [ ] **Step 5: Add A/B badge to `NoteRow`**

Still in `NotesView.tsx`, find the `NoteRow` function. Just after the "Note type badge" rendering (look for `NOTE_TYPE_COLORS`), add a layout badge before the fragment count:

```typescript
      {/* A/B layout badge */}
      <span
        className={`shrink-0 px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wide ${
          note.ux_variant === "B"
            ? "bg-violet-50 text-violet-700 border border-violet-200"
            : "bg-slate-50 text-slate-500 border border-slate-200"
        }`}
        title={note.ux_variant === "B" ? "New layout (experiment)" : "Classic layout"}
      >
        {note.ux_variant}
      </span>
```

Exact placement: put it immediately after the ticker-pills `<div>` closes and before the AI status badge, so the row reads `chevron → title → tickers → [A/B] → AI → frags → recording dot → date → delete`.

- [ ] **Step 6: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: no errors in our edited files. (Filter skips the pre-existing Next 15 params-Promise noise in `.next/types`.)

- [ ] **Step 7: Manual smoke check**

Start `npm run dev`, open the Notes tab, click **New Note**. The modal now shows the Layout block with A/B buttons. Create an A note and a B note. In the list, confirm the `[A]` and `[B]` badges appear next to the titles.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api/notesClient.ts frontend/src/components/domain/notes/NoteCreationModal.tsx frontend/src/app/\(dashboard\)/notes/NotesContainer.tsx frontend/src/app/\(dashboard\)/notes/NotesView.tsx
git commit -m "feat(notes): A/B variant picker in NoteCreationModal and list badge"
```

---

## Task 4: Frontend — extended Heading extension (with `sectionId` attr)

**Files:**
- Create: `frontend/src/components/domain/notes/sectionHeadingExtension.ts`
- Modify: `frontend/src/components/domain/notes/RichTextEditor.tsx`

Goal: carry a stable `sectionId` attribute on `<h2>` tags so the builder can find and replace a section by id without guessing from text content.

- [ ] **Step 1: Create the extension**

Create `frontend/src/components/domain/notes/sectionHeadingExtension.ts`:

```typescript
import Heading from "@tiptap/extension-heading";

/**
 * SectionHeading — TipTap Heading extended with a stable `sectionId` attr.
 *
 * Persisted to HTML as `data-section-id`. Used by the editor-section builder
 * to find an existing section heading and replace the content that follows
 * it, without resorting to matching on visible heading text (which the user
 * is allowed to edit).
 *
 * Only intended for h2 nodes in practice; the attribute is optional and
 * defaults to null for all other headings.
 */
export const SectionHeading = Heading.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      sectionId: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute("data-section-id"),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.sectionId ? { "data-section-id": String(attrs.sectionId) } : {},
      },
    };
  },
});

/** Valid section ids used by Variant B's auto-insert. */
export type SectionId = "user_notes" | "raw_transcript" | "polished_transcript";
```

- [ ] **Step 2: Wire it into `RichTextEditor`**

Edit `frontend/src/components/domain/notes/RichTextEditor.tsx`. Add the import near the other TipTap imports:

```typescript
import { SectionHeading } from "./sectionHeadingExtension";
```

Find the `useEditor` block and change the `StarterKit.configure` call to **disable** StarterKit's default heading, then include `SectionHeading` as a separate extension:

```typescript
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        // StarterKit includes: bold, italic, strike, code, codeBlock, blockquote,
        // bulletList, orderedList, listItem, horizontalRule, hardBreak, history
      }),
      SectionHeading.configure({ levels: [1, 2, 3] }),
      Placeholder.configure({
        placeholder: ({ node }) => {
          if (node.type.name === "heading") return "Heading";
          return "Write your notes here, or type / for commands…";
        },
      }),
      Typography,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      Image.configure({ inline: false }),
      slashExtension,
      Extension.create({
        name: "timestampHighlight",
        addProseMirrorPlugins() {
          return [timestampDecoPlugin];
        },
      }),
    ],
```

- [ ] **Step 3: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: no errors on our files. A pre-existing A note should still open and render (heading behaviour is identical; only the attr pipeline has changed).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/domain/notes/sectionHeadingExtension.ts frontend/src/components/domain/notes/RichTextEditor.tsx
git commit -m "feat(notes): SectionHeading TipTap extension with sectionId attr"
```

---

## Task 5: Frontend — `editorSectionBuilder` helper

**Files:**
- Create: `frontend/src/components/domain/notes/editorSectionBuilder.ts`

Pure TS helper: no React. Builds TipTap JSON nodes for each section and handles idempotent insertion into a TipTap `Editor` instance.

- [ ] **Step 1: Create the file**

Create `frontend/src/components/domain/notes/editorSectionBuilder.ts`:

```typescript
/**
 * editorSectionBuilder — TipTap JSON builders + insert-or-replace helper for
 * the three auto-generated sections that Variant B adds to the main editor:
 *
 *   - "user_notes"           — heading + divider (content is the user's own
 *                              notes; we never overwrite the body, only ensure
 *                              the heading/divider exist at the top)
 *   - "raw_transcript"       — heading + divider + bilingual/monolingual table
 *   - "polished_transcript"  — heading + divider + bilingual/monolingual table
 *
 * Section identity lives on the <h2> via the `sectionId` attr (see
 * sectionHeadingExtension.ts). Insertion logic walks the top-level doc
 * looking for that sectionId; if found, it replaces every node from the
 * matched heading up to (but not including) the next section heading — so
 * the user's edits *outside* that range are preserved. If not found, the
 * helper appends the section to the end of the document.
 */

import type { Editor } from "@tiptap/react";
import type { TranscriptLine, PolishedSegment } from "@/lib/api/notesClient";
import type { SectionId } from "./sectionHeadingExtension";

type Json = Record<string, unknown>;

// ---------------------------------------------------------------------------
// TipTap JSON builders (pure)
// ---------------------------------------------------------------------------

function textNode(text: string): Json {
  return { type: "text", text };
}

function paragraph(text: string): Json {
  return { type: "paragraph", content: text ? [textNode(text)] : [] };
}

function headerCell(text: string): Json {
  return {
    type: "tableHeader",
    content: [paragraph(text)],
  };
}

function dataCell(text: string): Json {
  return {
    type: "tableCell",
    content: [paragraph(text)],
  };
}

function row(cells: Json[]): Json {
  return { type: "tableRow", content: cells };
}

function sectionHeading(sectionId: SectionId, text: string): Json {
  return {
    type: "heading",
    attrs: { level: 2, sectionId },
    content: [textNode(text)],
  };
}

function horizontalRule(): Json {
  return { type: "horizontalRule" };
}

/**
 * Build the TipTap table JSON for a bilingual transcript.
 * If `bilingual` is true, renders 3 columns: Time | Original | English.
 * Otherwise 2 columns: Time | Text.
 */
export function buildBilingualTableJson(
  rows: { timestamp: string; textOriginal: string; textEnglish: string }[],
  bilingual: boolean,
): Json {
  const header = bilingual
    ? row([headerCell("Time"), headerCell("原文"), headerCell("English")])
    : row([headerCell("Time"), headerCell("Text")]);

  const bodyRows = rows.map((r) =>
    bilingual
      ? row([dataCell(r.timestamp), dataCell(r.textOriginal), dataCell(r.textEnglish)])
      : row([dataCell(r.timestamp), dataCell(r.textOriginal)]),
  );

  return { type: "table", content: [header, ...bodyRows] };
}

/**
 * Build the nodes for the raw-transcript section (heading + hr + table).
 * Expects `lines` in the shape the WebSocket + DB carry.
 */
export function buildRawTranscriptSectionNodes(lines: TranscriptLine[]): Json[] {
  // Skip interim lines — only final ones get persisted as part of the editor.
  const finalLines = lines.filter((l) => !l.is_interim);

  // Detect bilingual: any line carries a non-empty `translation` and a non-English language.
  // Access via a loose cast because the core TranscriptLine type doesn't declare these two
  // optional fields today (they are added at runtime by the live_v2 transcript messages).
  const loose = finalLines as unknown as { translation?: string; language?: string; timestamp: string; text: string }[];
  const bilingual = loose.some((l) => Boolean(l.translation) && l.language && l.language !== "en");

  const rows = loose.map((l) => ({
    timestamp: l.timestamp,
    textOriginal: l.text ?? "",
    textEnglish: l.translation ?? "",
  }));

  return [
    horizontalRule(),
    sectionHeading("raw_transcript", "Raw Live Transcript"),
    buildBilingualTableJson(rows, bilingual),
  ];
}

/** Build the nodes for the polished-transcript section. */
export function buildPolishedTranscriptSectionNodes(
  segments: PolishedSegment[],
  bilingual: boolean,
): Json[] {
  const rows = segments.map((s) => ({
    timestamp: s.timestamp,
    textOriginal: s.text_original,
    textEnglish: s.text_english,
  }));

  return [
    horizontalRule(),
    sectionHeading("polished_transcript", "Polished Transcript"),
    buildBilingualTableJson(rows, bilingual),
  ];
}

/**
 * Build a minimal "Your Notes" heading + hr. We do NOT include the body — the
 * user's own content stays untouched. This is used only when no user_notes
 * heading exists yet (first-ever insert for a B note); insertOrReplaceSection
 * then places these two nodes at the very top.
 */
export function buildUserNotesHeadingNodes(): Json[] {
  return [sectionHeading("user_notes", "Your Notes")];
}

// ---------------------------------------------------------------------------
// Insert or replace logic
// ---------------------------------------------------------------------------

/**
 * Find the index (in `doc.content`) of the heading node carrying sectionId.
 * Returns -1 if none found.
 */
function findSectionIndex(doc: Json, sectionId: SectionId): number {
  const content = (doc.content as Json[] | undefined) ?? [];
  return content.findIndex((node) => {
    if (node?.type !== "heading") return false;
    const attrs = node.attrs as Json | undefined;
    return attrs?.sectionId === sectionId;
  });
}

/**
 * Find the index of the next section heading after `fromIndex` (exclusive).
 * Returns `content.length` if none found — meaning the current section runs
 * to the end of the document.
 */
function findNextSectionIndex(doc: Json, fromIndex: number): number {
  const content = (doc.content as Json[] | undefined) ?? [];
  for (let i = fromIndex + 1; i < content.length; i++) {
    const node = content[i];
    if (node?.type === "heading") {
      const attrs = node.attrs as Json | undefined;
      if (attrs?.sectionId) return i;
    }
  }
  return content.length;
}

/**
 * Replace or append the nodes belonging to `sectionId`. `nodes` should include
 * the section heading itself plus all following content for that section
 * (typically an hr in front, heading, then a table).
 *
 * Special case: `sectionId === "user_notes"` does NOT overwrite existing user
 * content — it only prepends the heading if no user_notes heading is found.
 */
export function insertOrReplaceSection(
  editor: Editor,
  sectionId: SectionId,
  nodes: Json[],
): void {
  const doc = editor.getJSON() as Json;
  const content = ((doc.content as Json[] | undefined) ?? []).slice();

  const matchIndex = findSectionIndex(doc, sectionId);

  if (sectionId === "user_notes") {
    // Never overwrite user's body. If heading missing, prepend just the heading.
    if (matchIndex === -1) {
      const newContent = [...nodes, ...content];
      editor.commands.setContent({ ...doc, content: newContent } as Json, false);
    }
    return;
  }

  if (matchIndex === -1) {
    // No existing section — append to end.
    const newContent = [...content, ...nodes];
    editor.commands.setContent({ ...doc, content: newContent } as Json, false);
    return;
  }

  // Replace from matchIndex up to (but not including) the next section heading.
  // We also drop the preceding <hr> if it was inserted by a previous call
  // (matchIndex - 1 is an hr we own), so the new hr in `nodes` takes its place.
  let start = matchIndex;
  if (matchIndex > 0 && content[matchIndex - 1]?.type === "horizontalRule") {
    start = matchIndex - 1;
  }
  const end = findNextSectionIndex(doc, matchIndex);

  const newContent = [...content.slice(0, start), ...nodes, ...content.slice(end)];
  editor.commands.setContent({ ...doc, content: newContent } as Json, false);
}
```

- [ ] **Step 2: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: no errors in `editorSectionBuilder.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/domain/notes/editorSectionBuilder.ts
git commit -m "feat(notes): editorSectionBuilder — TipTap section builders + insert/replace"
```

---

## Task 6: Frontend — `RecordingPanel` forwards polished segments

**Files:**
- Modify: `frontend/src/components/domain/notes/RecordingPanel.tsx`

Goal: when Gemini polish completes, hold onto the structured `segments` + `is_bilingual` + `key_topics` from the WebSocket message, and forward them to `onComplete` so the container can build the polished section.

- [ ] **Step 1: Extend `Props.onComplete` signature and local state**

Edit `frontend/src/components/domain/notes/RecordingPanel.tsx`.

Near the other imports, add `PolishedSegment`:

```typescript
import { notesClient, type TranscriptLine, type PolishedSegment } from "@/lib/api/notesClient";
```

Replace the existing `Props.onComplete` signature:

```typescript
interface Props {
  noteId: string;
  onClose: () => void;
  onComplete: (
    lines: TranscriptLine[],
    durationSeconds: number,
    polished: {
      segments: PolishedSegment[];
      language: string;
      is_bilingual: boolean;
      key_topics: string[];
    } | null,
  ) => void;
}
```

Add new state next to `polishedText`:

```typescript
  const [polishedText, setPolishedText] = useState<string | null>(null);
  const [polishedSegments, setPolishedSegments] = useState<PolishedSegment[]>([]);
  const [polishedLanguage, setPolishedLanguage] = useState<string>("");
  const [polishedIsBilingual, setPolishedIsBilingual] = useState<boolean>(false);
  const [polishedKeyTopics, setPolishedKeyTopics] = useState<string[]>([]);
```

- [ ] **Step 2: Capture structured fields from the WebSocket message**

Still in `RecordingPanel.tsx`, find the `onmessage` handler branch for `msg.type === "polished_transcript"`:

```typescript
        } else if (msg.type === "polished_transcript") {
          setPolishedText(msg.text);
          setPolishedSegments(Array.isArray(msg.segments) ? msg.segments : []);
          setPolishedLanguage(typeof msg.language === "string" ? msg.language : "");
          setPolishedIsBilingual(Boolean(msg.is_bilingual));
          setPolishedKeyTopics(Array.isArray(msg.key_topics) ? msg.key_topics : []);
          setStatus("idle");
          setIsRecording(false);
```

- [ ] **Step 3: Pass the polished payload through `onComplete`**

Update `handlePolishedDone` to include the polished payload:

```typescript
  const handlePolishedDone = useCallback(() => {
    const finalLines = lines.filter((l) => !l.is_interim);
    onComplete(finalLines, duration, {
      segments: polishedSegments,
      language: polishedLanguage,
      is_bilingual: polishedIsBilingual,
      key_topics: polishedKeyTopics,
    });
  }, [lines, duration, onComplete, polishedSegments, polishedLanguage, polishedIsBilingual, polishedKeyTopics]);
```

And update the "save audio only" path inside `stopRecording` to pass `null`:

```typescript
    } else {
      setStatus("idle");
      setIsRecording(false);
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop_no_polish" }));
        setTimeout(() => wsRef.current?.close(), 500);
      }
      const finalLines = lines.filter((l) => !l.is_interim);
      onComplete(finalLines, duration, null);
    }
```

- [ ] **Step 4: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: `RecordingPanel.tsx` clean. `NotesEditorView.tsx` will now flag a type mismatch on `onRecordingComplete` — we fix that in Task 7.

- [ ] **Step 5: No commit yet**

Task 6 + Task 7 are logically one change (onComplete signature breaks `NotesEditorView` / `NotesEditorContainer` together). Commit at the end of Task 7.

---

## Task 7: Frontend — Container inserts sections on Save Both (B only)

**Files:**
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx`

Goal: when Save Both fires on a B note, build the three section blocks (`user_notes` heading, `raw_transcript` section with table, `polished_transcript` section with table) and insert them into the note's editor content. Uses the existing auto-save path to persist — no new endpoints.

We need a handle to the TipTap `Editor` instance in the container, because `insertOrReplaceSection` mutates a live editor. The cleanest way is to forward an editor ref up from `RichTextEditor` through `NotesEditorView`.

- [ ] **Step 1: Expose an editor ref from `RichTextEditor`**

Edit `frontend/src/components/domain/notes/RichTextEditor.tsx`. At the top of the props interface, add the optional ref prop:

Find the existing `interface Props` (or equivalent) for `RichTextEditor`. Add the new prop:

```typescript
interface RichTextEditorProps {
  initialContent?: Record<string, unknown>;
  onChange: (json: Record<string, unknown>, plainText: string) => void;
  onTimestampClick?: (seconds: number) => void;
  onEditorReady?: (editor: Editor) => void;
}
```

(If the existing interface uses different prop names, **keep them** and only add `onEditorReady`.)

Inside the component, after the `useEditor` hook, add an effect to fire the ready callback once the editor instance exists:

```typescript
  useEffect(() => {
    if (editor && onEditorReady) onEditorReady(editor);
  }, [editor, onEditorReady]);
```

Make sure `onEditorReady` is destructured from the component's props.

- [ ] **Step 2: Update `NotesEditorView` types and forward the callback**

Edit `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`.

Change the import at the top to pick up `Editor` (and the polished-payload type):

```typescript
import type { Editor } from "@tiptap/react";
import type { NoteStub, TranscriptLine, PolishedSegment } from "@/lib/api/notesClient";
```

Extend `Props.onRecordingComplete`:

```typescript
  onRecordingComplete: (
    lines: TranscriptLine[],
    durationSeconds: number,
    polished: {
      segments: PolishedSegment[];
      language: string;
      is_bilingual: boolean;
      key_topics: string[];
    } | null,
  ) => void;
```

Add a new prop:

```typescript
  onEditorReady: (editor: Editor) => void;
```

Thread `onEditorReady` into the `<RichTextEditor />` render:

```typescript
            <RichTextEditor
              initialContent={note.editor_content}
              onChange={onContentChange}
              onTimestampClick={note.recording_path ? handleTimestampSeek : undefined}
              onEditorReady={onEditorReady}
            />
```

- [ ] **Step 3: Wire the container to capture the editor + insert sections**

Edit `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx`.

Add imports at the top:

```typescript
import type { Editor } from "@tiptap/react";
import type { PolishedSegment, TranscriptLine } from "@/lib/api/notesClient";
import {
  buildRawTranscriptSectionNodes,
  buildPolishedTranscriptSectionNodes,
  buildUserNotesHeadingNodes,
  insertOrReplaceSection,
} from "@/components/domain/notes/editorSectionBuilder";
```

Add a ref for the editor at the top of the component body, alongside the existing `saveTimer` ref:

```typescript
  const editorRef = useRef<Editor | null>(null);
  const handleEditorReady = useCallback((editor: Editor) => {
    editorRef.current = editor;
  }, []);
```

Rewrite `handleRecordingComplete` to (a) save the transcript (current behaviour), (b) when variant is B and a polished payload is present, insert the three sections into the editor:

```typescript
  const handleRecordingComplete = useCallback(
    async (
      lines: TranscriptLine[],
      durationSeconds: number,
      polished: {
        segments: PolishedSegment[];
        language: string;
        is_bilingual: boolean;
        key_topics: string[];
      } | null,
    ) => {
      if (!note) return;

      const res = await notesClient.saveTranscript(note.note_id, lines, durationSeconds);
      if (res.success && res.data) {
        setNote(res.data);
        updateNote(res.data);
      } else {
        patchNote({
          transcript_lines: lines,
          duration_seconds: durationSeconds,
          summary_status: "awaiting_speakers",
        });
      }

      // Auto-insert the three sections into the main editor (both variants)
      // so the raw transcript + polished transcript live alongside the user's
      // notes. This fixes the prior A-variant bug where the polished output
      // had no persistent display.
      if (editorRef.current) {
        const editor = editorRef.current;
        // Order of operations matters: user_notes first so the heading lands
        // at the very top, then raw below, then polished below that.
        insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
        insertOrReplaceSection(
          editor,
          "raw_transcript",
          buildRawTranscriptSectionNodes(lines),
        );
        if (polished && polished.segments.length > 0) {
          insertOrReplaceSection(
            editor,
            "polished_transcript",
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual),
          );
        }
        // insertOrReplaceSection fires the editor's onUpdate synchronously;
        // that triggers our normal content change handler which patches
        // editor_content into local state, which in turn flips isDirty and
        // schedules an auto-save. No extra save call is needed.
      }

      setShowRecordingPopup(false);
    },
    [note, setNote, updateNote, patchNote, setShowRecordingPopup],
  );
```

Pass `handleEditorReady` into the view render:

```typescript
  return (
    <NotesEditorView
      note={note}
      isSaving={isSaving}
      showRecordingPopup={showRecordingPopup}
      onBack={() => router.push("/notes")}
      onTitleChange={handleTitleChange}
      onContentChange={handleContentChange}
      onOpenRecording={() => setShowRecordingPopup(true)}
      onCloseRecording={() => setShowRecordingPopup(false)}
      onRecordingComplete={handleRecordingComplete}
      onSaveSpeakers={handleSaveSpeakers}
      onExtractTopics={handleExtractTopics}
      onDelta={handleDelta}
      onStartAISummary={handleStartAISummary}
      onEditorReady={handleEditorReady}
    />
  );
```

- [ ] **Step 4: Type-check**

Run:

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -40
```

Expected: clean. If anything complains, the likely culprit is a missing `useCallback`/`useRef` import at the top of `NotesEditorContainer.tsx`; ensure:

```typescript
import { useEffect, useCallback, useRef } from "react";
```

- [ ] **Step 5: Commit (Tasks 6 + 7 together)**

```bash
git add frontend/src/components/domain/notes/RecordingPanel.tsx frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorView.tsx frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorContainer.tsx frontend/src/components/domain/notes/RichTextEditor.tsx
git commit -m "feat(notes): auto-insert transcript sections into editor for variant B"
```

---

## Task 8: Manual smoke test

**Files:** none modified.

- [ ] **Step 1: Restart backend + frontend**

```bash
# Terminal 1
cd backend && uvicorn app.main:app --reload --port 8000
# Terminal 2
cd frontend && npm run dev
```

- [ ] **Step 2: Verify A notes now show transcripts in the editor**

Navigate to `http://localhost:3000/notes`. Open a pre-existing A note (badge shows `[A]`), or create a new one (layout defaults to A). Record a short (~10 s) system-audio clip in Japanese or Chinese, click **Stop & AI Polish**, click **Save Both**. Confirm:
  - [ ] Sidebar switches into the wizard (Speaker → Topics → …) exactly as before — A's sidebar is unchanged.
  - [ ] The main editor **now contains** three sections under `## Your Notes`, `## Raw Live Transcript`, `## Polished Transcript` (previously the polished output was lost).
  - [ ] A note library view still shows `[A]` next to the title.

- [ ] **Step 3: Create and exercise a B note — editor behaviour is identical to A**

Click **New Note**. Fill in title, type, ticker. In the new **Layout** block, pick **B — New**. Create. Confirm:
  - [ ] List view shows `[B]` next to the new title.
  - [ ] Editor opens. Type a short sentence as your own notes.
  - [ ] Click **Record Audio**. Sidebar shows `RecordingPanel` (same as A, unchanged in Plan 1).
  - [ ] Record ~10 s in Japanese using System Audio, click **Stop & AI Polish**.
  - [ ] Wait ~30 s for polish.
  - [ ] Click **Save Both (Draft + Polished)**.
  - [ ] Editor content looks identical to the A note from Step 2 — same three sections. (The B-vs-A sidebar divergence lands in Plan 2; in Plan 1 they look the same outside the library badge.)

- [ ] **Step 4: Verify the editor now contains three sections**

In the main editor:
  - [ ] Your notes appear under `## Your Notes` at the top.
  - [ ] A horizontal rule separates them from the next section.
  - [ ] `## Raw Live Transcript` appears, followed by a 3-column table `Time | 原文 | English` with one row per final transcript line.
  - [ ] Another horizontal rule.
  - [ ] `## Polished Transcript` appears, followed by a 3-column table with the polished segments.
  - [ ] You can click into any cell and type — edits auto-save.
  - [ ] Reload the page; all three sections survive (editor content round-trips through the DB).

- [ ] **Step 5: Verify English-only B notes render monolingual tables**

Create another B note. Record a short English clip with System Audio + `Language: English`. Click Stop & AI Polish → Save Both. Confirm:
  - [ ] Raw Transcript table is 2 columns (`Time | Text`) — no `English` column.
  - [ ] Polished Transcript table is 2 columns.

- [ ] **Step 6: Verify PostMeetingWizard still works on B notes**

On the same B note from Step 3, the sidebar now shows the wizard (Speakers step). Skip → accept auto-derived topics → let extraction run → review deltas (likely none). Confirm:
  - [ ] Wizard progresses through all steps without error.
  - [ ] The previously-inserted editor sections are unaffected by wizard actions.

- [ ] **Step 7: No commit for this task**

---

## Self-Review Checklist

**Spec coverage** (from `docs/superpowers/specs/2026-04-22-meeting-intelligence-ab-design.md` §10 Plan 1):
- "Add `ux_variant`, migration, NoteCreationModal picker, library badge." → Tasks 1 + 3.
- "Add polish prompt change + structured segments persistence." → Task 2.
- "Add `BilingualTranscriptTable` + `editorSectionBuilder`." → Tasks 4 + 5. (Spec mentioned `BilingualTranscriptTable.tsx` as a separate file; this plan collapses the table-builder into `editorSectionBuilder.ts` because it's a ~15 line pure function with no React concerns. The behaviour and exported functions match what the spec needed.)
- "Wire Save Both in variant B to insert the three editor sections." → Tasks 6 + 7.
- "Verification: create a B note, record a short JA clip, click Save Both, confirm three sections appear with bilingual tables; confirm A notes still behave exactly as today." → Task 8 Steps 3-6.

**Placeholder scan:** no TBDs / TODOs / "add appropriate X" / "similar to Task N" patterns. Every step has the exact code or command.

**Type / name consistency:**
- `ux_variant` / `"A" | "B"` used consistently across ORM, domain, service, endpoint, client, modal, list, container, view.
- `PolishedSegment` shape (`{timestamp, speaker, text_original, text_english}`) consistent between backend `_parse_polish_response`, WebSocket message, TypeScript interface, and builder.
- `SectionId` (`"user_notes" | "raw_transcript" | "polished_transcript"`) consistent between extension, builder, and container.
- Helper function names (`buildRawTranscriptSectionNodes`, `buildPolishedTranscriptSectionNodes`, `buildUserNotesHeadingNodes`, `insertOrReplaceSection`) used identically in Task 5 (definition) and Task 7 (call sites).
- `_parse_polish_response` / `_flatten_segments_to_markdown` used identically in Task 2 Step 1 (tests) and Step 3 (implementation).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-22-meeting-intelligence-plan-1-variant-infra.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
