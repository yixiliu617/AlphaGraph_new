# Meeting Intelligence — Variant B Design Spec (A/B Test)

**Date:** 2026-04-22
**Status:** Approved — ready for implementation planning

---

## Goal

Ship an A/B-testable "Variant B" for the Notes editor that changes post-recording UX without disturbing the current behaviour ("Variant A").

**What applies to both variants** (shared improvements — fixes the current A-note data-loss bug where polished output has nowhere to go):
- Auto-inserts bilingual raw + polished transcripts into the main editor below the user's own notes on Save Both.
- Allows extraction to proceed with zero user-supplied topics (derived from the user's own notes + transcript — already shipped).

**What distinguishes Variant B from A:**
- B replaces the sidebar's post-wizard state with a Meeting Intelligence panel containing the extraction results plus a chat agent. (Plans 2 + 3.)
- A keeps the existing sidebar behaviour (PostMeetingWizard → completion message).

Variant A's sidebar is unchanged from today. The user opts into B per note at creation time.

---

## 1. A/B Variant Mechanism

### 1.1 Storage

- New column on `MeetingNoteORM`: `ux_variant: String default="A"`.
- Add matching field on `MeetingNote` domain model: `ux_variant: Literal["A", "B"] = "A"`.
- Migration: `ALTER TABLE meeting_notes ADD COLUMN ux_variant VARCHAR DEFAULT 'A'`.

### 1.2 Selection

- `NoteCreationModal` gets a variant picker:
  ```
  Layout:  ( • ) A — Classic (wizard in sidebar)
           ( ) B — New (transcripts in editor + chat)
  ```
- `CreateNoteRequest` on the backend gains `ux_variant: Literal["A", "B"] = "A"`.
- Notes library list shows a small `[A]` / `[B]` badge next to each title.

### 1.3 Branching rule

Every new code path added by this spec is gated behind `note.ux_variant === "B"`. For A notes, the UI, endpoints and persistence paths are bit-for-bit identical to today.

### 1.4 What is NOT in scope for the A/B mechanism

- Side-by-side dual rendering of the same note in both variants (would require overlay rendering; rejected for complexity).
- A "clone as variant B" action to copy transcripts from an A note into a new B note. Deferred — add only if real testing reveals we want it.
- A global default override or app-level toggle. Per-note only.

---

## 2. Editor Layout (applies to both variants)

### 2.1 Three-section document structure

The editor (TipTap) contains three sibling sections with stable identifiers:

```
┌──────────────────────────────────────┐
│ ## Your Notes                         │ data-section-id="user_notes"
│  (what the user typed; always first)  │
│  <hr>                                 │
│ ## Raw Live Transcript                │ data-section-id="raw_transcript"
│  [3-col table if non-EN, 2-col if EN] │
│  | Time  | 原文       | English    |  │
│  | 00:15 | 売上高は…  | Revenue…   |  │
│  <hr>                                 │
│ ## Polished Transcript                │ data-section-id="polished_transcript"
│  [3-col table if non-EN, 2-col if EN] │
│  | Time  | 原文       | English    |  │
│  | 00:15 | 売上高は…  | Revenue…   |  │
└──────────────────────────────────────┘
```

- **Divider:** TipTap `<hr>`.
- **Table:** `@tiptap/extension-table` (already a dependency). Time column 80 px; other columns `1fr`. Time column sticky-left. Table header row bold with subtle border.
- **Bilingual decision rule:** if the source language is one of `zh`, `ja`, `ko`, render 3-column `Time | Original | English`. If English, render 2-column `Time | Text`.

### 2.2 Editability

Every section is a normal editable TipTap node. The user can:
- Rewrite their notes.
- Fix a misheard word in the raw transcript.
- Reword a phrase in the polished transcript.
- Delete rows, add rows, add their own commentary between transcript rows.

Edits auto-save via the existing `PUT /notes/{id}` path writing `editor_content` + `editor_plain_text`.

### 2.3 Source of truth for AI tools

AI tools (wizard extraction, chat agent, analysis modules) read from the **authoritative DB fields**:
- `transcript_lines` — raw transcript (with translations)
- `polished_transcript_meta.segments` — structured polished segments
- `polished_transcript` — polished markdown (for copy/export)

AI tools do **not** read from `editor_content`. Editor edits are display-only and do not back-propagate to the source fields. This is a known limitation of Q3-B (fully editable). A later iteration can add a "Re-sync from source" button per section.

### 2.4 Insert-or-replace-in-place helper

Utility `editorSectionBuilder.insertOrReplaceSection(editor, sectionId, contentJson)`:

- Walks the TipTap doc looking for a node with `data-section-id=sectionId`.
- If found → replaces its contents in place (preserving any user edits above/below).
- If not found → appends: `<hr>` + `<h2>` heading + the content block, with the wrapping `data-section-id` attribute.
- For `user_notes`, the function ensures a heading/divider exists at the top but never overwrites the body.

### 2.5 Save Both flow (both variants)

When the user clicks **Save Both (Draft + Polished)** in `RecordingPanel`:

1. Frontend calls `notesClient.saveTranscript(note_id, lines, duration)` — already exists.
2. Frontend builds TipTap JSON for both transcript sections using `BilingualTranscriptTable` helpers:
   - Raw section from `lines` (using `timestamp`, `text`, `translation`, `language` per line).
   - Polished section from the `polished_transcript` WebSocket message, which is extended by this spec to include `segments: [...]`. If the WS message is not available for any reason (e.g. user reloaded after polish), the container re-fetches the note via `GET /notes/{id}` and reads `polished_transcript_meta.segments`.
3. Frontend calls `insertOrReplaceSection` three times (user_notes, raw_transcript, polished_transcript).
4. Existing auto-save debounces and writes the new `editor_content` via `PUT /notes/{id}`.
5. `summary_status` flips through the wizard as today (`awaiting_speakers` → … → `complete`).

The flow is identical for both variants; the editor auto-insert is a shared improvement that resolves the prior A-variant bug where the polished transcript had nowhere to go.

---

## 3. Polish Prompt Change

### 3.1 Structured output

`gemini_batch_transcribe` (in `backend/app/services/live_transcription.py`) changes its output shape from free-form markdown to structured JSON:

```json
{
  "language": "ja",
  "is_bilingual": true,
  "key_topics": ["revenue guidance", "ARM relationship", "capex"],
  "segments": [
    {
      "timestamp": "00:15",
      "speaker": "Tanaka (CFO)",
      "text_original": "売上高は前年比20%増となりました。",
      "text_english": "Revenue grew 20% year-over-year."
    }
  ],
  "input_tokens": 1234,
  "output_tokens": 5678
}
```

### 3.2 Prompt rewrite

New prompt (abridged):

> Transcribe this financial meeting. Primary language: {lang_name} with English code-switching.
>
> Return valid JSON with: `language`, `is_bilingual`, `key_topics` (list of short strings), `segments` (list of `{timestamp, speaker, text_original, text_english}`).
>
> Rules:
> 1. Timestamps in `MM:SS` format.
> 2. Provide `text_english` for every segment. For English-only meetings, `text_english` equals `text_original`.
> 3. Never repeat. If audio unclear, write `[audio unclear]`.

Token budget raised to `maxOutputTokens: 65536` (unchanged).

### 3.3 Persistence

On WebSocket polish completion in `_run_live_v2_session`:
- `polished_transcript`: flattened markdown form of the segments (backward-compat display/export).
- `polished_transcript_language`: `language` field from JSON.
- `polished_transcript_meta`: the full JSON plus `{ran_at, model: "gemini-2.5-flash"}`.

The outgoing WebSocket `polished_transcript` message is extended to carry the parsed segments so B clients can render the bilingual table immediately without a round-trip:

```json
{
  "type": "polished_transcript",
  "text": "<flattened markdown>",
  "language": "ja",
  "is_bilingual": true,
  "segments": [{"timestamp": "00:15", "speaker": "...", "text_original": "...", "text_english": "..."}],
  "key_topics": ["..."],
  "input_tokens": 1234,
  "output_tokens": 5678
}
```

Applies to both A and B notes; A notes simply ignore `segments` / `key_topics`.

### 3.4 Backward compatibility

Existing A notes with a pre-change `polished_transcript` (free-form markdown, no `segments`) continue to render their markdown as-is in the sidebar preview. B notes created after the prompt change get structured segments.

---

## 4. Sidebar Lifecycle (Variant B)

Right-panel branch order in `NotesEditorView` (B notes only; A follows today's rules):

| Condition | Component |
|---|---|
| `showRecordingPopup === true` | `RecordingPanel` *(existing)* |
| `summary_status ∈ {awaiting_speakers, awaiting_topics, extracting, awaiting_approval}` | `PostMeetingWizard` *(existing)* |
| `summary_status === "complete"` | **`MeetingIntelligencePanel`** *(new)* |
| else | `NoteSearchPanel` *(existing)* |

### 4.1 `MeetingIntelligencePanel` layout

Single scrollable panel, sticky chat input at the bottom:

```
┌───────────────────────────┐
│ ▾ AI Extraction            │   ← collapsible, expanded by default
│   Narrative summary        │
│   ▸ Topic: ARM             │   ← click to expand TopicFragment card
│   ▸ Topic: guidance        │
│   ▸ Delta: margin shift    │   ← non-dismissed delta cards
│   ▸ Action items           │
│                            │
├───────────────────────────┤
│ 💬 Chat                    │
│  user: bull/bear points?   │
│  agent: Bulls say X...     │
│    [tool: bull_bear ✓]     │   ← small inline tool-call badge
│  user: what were the       │
│        key numbers?        │
│  agent: ...                │
│                            │
├─ sticky ──────────────────┤
│ [ ask about this note… ]   │
│                 [Send]     │
└───────────────────────────┘
```

The AI Extraction group is rendered from `note.ai_summary` (existing structure). It is read-only in B (same as A's `CompleteStep`).

---

## 5. Chat Agent (Variant B)

### 5.1 Data model

New ORM column: `chat_messages: JSON default=[]` on `MeetingNoteORM`.
Domain field: `chat_messages: list[ChatMessage] = []` on `MeetingNote`.

Message shape:
```python
class ChatMessage(BaseModel):
    id: str              # uuid
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[ToolCall] = []   # for assistant messages
    created_at: datetime

class ToolCall(BaseModel):
    tool: str
    args: dict
    result: str           # truncated tool output for display
```

### 5.2 LLM

- Provider: Gemini, via `GeminiAdapter.generate_with_tools` (already implemented).
- Model: the adapter's default (currently Gemini 2.5 Flash — same as polish and translation paths).
- Agent loop: classic tool-use until no tool calls are returned or a max-iterations guard hits.

### 5.3 Tools

Implemented in `backend/app/services/chat/chat_tools.py`:

| Tool | Purpose | Implementation |
|---|---|---|
| `get_user_notes()` | Return `editor_plain_text` | Direct DB read |
| `get_raw_transcript(bilingual=True)` | Return formatted raw transcript string | Format `transcript_lines` with timestamps |
| `get_polished_transcript()` | Return polished markdown | Direct DB read |
| `get_note_metadata()` | Return tickers, date, note_type, duration, language | Direct DB read |
| `list_previous_notes_for_ticker(ticker)` | List prior notes for this ticker | DB query, return `[{note_id, title, meeting_date, note_type}]` sorted by date desc |
| `run_analysis(module, key_focus=None)` | Run one analysis module | One Gemini 2.5 Flash call using the module's prompt template against the polished transcript (fallback to raw if polished missing). Cache result in `analysis_jobs[module]` to avoid re-runs within a single note. |
| `compare_vs_previous(previous_note_id)` | Generate comparison markdown | Load both transcripts, run comparison prompt, return markdown |

**Modules** (initial set for `run_analysis`):
- `summary` — 5-7 bullet overview.
- `bull_bear` — bullish vs bearish arguments per company.
- `facts` — factual updates per company (guidance, launches, personnel).
- `catalysts` — past events + upcoming catalysts per company.
- `numbers` — every number mentioned with its context.
- `pecking_order` — analyst's ranking of companies with rationale.
- `action_items` — follow-ups, deadlines, tasks.

Each module's prompt template lives in `backend/app/services/chat/modules.py` as a constant. `ANALYSIS_MODULES: dict[str, ModuleSpec]`.

**Previous-notes loading is on-demand.** The chat agent's initial context is the current note only (via `get_*` tools). Prior notes are loaded only when the agent calls `list_previous_notes_for_ticker` or `compare_vs_previous`.

### 5.4 Result caching

Cache column: `analysis_jobs: JSON default={}` on `MeetingNoteORM`, keyed by module id:
```json
{
  "bull_bear": {
    "markdown": "...",
    "ran_at": "2026-04-22T15:00:00Z",
    "input_tokens": 1200,
    "output_tokens": 800,
    "prompt_version": "v1",
    "key_focus": null
  }
}
```

`run_analysis(module)` returns the cached entry if present and the `key_focus` matches. `run_analysis(module, force=True)` — add only if needed later; MVP is a cache-hit-or-run.

### 5.5 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/notes/{id}/chat` | List `chat_messages` for this note |
| `POST` | `/notes/{id}/chat` | Body `{message: str}` — runs the agent loop server-side, appends user + assistant messages, returns the full updated message list |
| `DELETE` | `/notes/{id}/chat` | Clear history |

MVP is non-streaming: POST blocks for the full agent loop (typically 2-15 s depending on tool calls) then returns. Frontend shows a spinner. SSE streaming is a v2 enhancement — noted, not in this spec.

### 5.6 System prompt

```
You help the user understand this meeting note. Use tools to read their notes,
the raw transcript, and the polished transcript. For specific analyses
(bull/bear, key numbers, catalysts, facts, action items, pecking order, summary)
call run_analysis with the appropriate module name. When comparing with prior
meetings, call list_previous_notes_for_ticker first to get note_ids, then
compare_vs_previous. Quote timestamps ([MM:SS]) when citing a specific
statement. Be concise.
```

---

## 6. Blank-topics Behaviour (Applies to BOTH A and B)

This was fixed pre-spec and is already deployed, but documented here for completeness.

- `PostMeetingWizard.TopicsStep`: Extract button enabled at zero topics. Label switches to "Extract (auto-derive from notes + transcript) →".
- `POST /notes/{id}/summary/extract`: no longer rejects empty `topics` list.
- `MeetingSummaryService._derive_topics_from_context`: new method that prompts the LLM with the user's `editor_plain_text` (primary) + transcript excerpt (fallback context), returning 3-6 derived topics.
- `extract_topic_fragments`: if `user_topics` is empty, calls `_derive_topics_from_context` first.

---

## 7. Data Model Summary

All new / modified fields on `MeetingNoteORM` introduced by this spec:

| Column | Type | Default | Status |
|---|---|---|---|
| `polished_transcript` | Text | null | Already added |
| `polished_transcript_language` | String | null | Already added |
| `polished_transcript_meta` | JSON | null | Already added; schema now contains `segments[]` + `key_topics[]` |
| `ux_variant` | String | `"A"` | **New** — this spec |
| `chat_messages` | JSON | `[]` | **New** — this spec |
| `analysis_jobs` | JSON | `{}` | **New** — this spec |

Domain model `MeetingNote` mirrors these with matching Pydantic types.

Migration approach: one-shot `ALTER TABLE` script (matching the pattern used for the polished-transcript columns).

---

## 8. Frontend Components

### 8.1 New files

- `frontend/src/components/domain/notes/MeetingIntelligencePanel.tsx` — sidebar post-wizard container.
- `frontend/src/components/domain/notes/ChatAgent.tsx` — chat UI (messages + input + tool-call badges), used inside `MeetingIntelligencePanel`.
- `frontend/src/components/domain/notes/BilingualTranscriptTable.tsx` — shared table renderer (pure function → TipTap JSON). Used by `editorSectionBuilder` and optionally by `RecordingPanel` live view for consistency.
- `frontend/src/components/domain/notes/editorSectionBuilder.ts` — `insertOrReplaceSection(editor, id, contentJson)` helper.

### 8.2 Modified files

- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — add the 4th sidebar branch gated on `note.ux_variant === "B"`.
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx` — on Save Both for B notes, build transcript sections and insert into editor before the wizard kicks off.
- `frontend/src/components/domain/notes/RecordingPanel.tsx` — pass polished segments to `onComplete` so the container can build the polished section; render a bilingual preview in the sidebar during recording (shared with the final table).
- `frontend/src/components/domain/notes/NoteCreationModal.tsx` — add variant radio picker.
- `frontend/src/app/(dashboard)/notes/NotesView.tsx` — add `[A]`/`[B]` badge next to each note title.
- `frontend/src/lib/api/notesClient.ts` — chat endpoints + polished-segments types + `ux_variant` field on `NoteStub`.

---

## 9. Backend Files

### 9.1 New

- `backend/app/services/chat/` package
  - `chat_service.py` — agent loop, message persistence
  - `chat_tools.py` — tool implementations
  - `modules.py` — analysis module registry with prompt templates
- `backend/app/api/routers/v1/notes_chat.py` — chat endpoints (or extend `notes.py`; decide during plan)

### 9.2 Modified

- `backend/app/services/live_transcription.py` — polish prompt rewrite, structured output parsing
- `backend/app/api/routers/v1/notes.py` — structured polished-transcript persistence path; add `ux_variant` to `CreateNoteRequest`; include new columns in serialisation
- `backend/app/services/notes_service.py` — `_to_orm` / `_to_domain` updates for new columns
- `backend/app/models/orm/note_orm.py` — new columns
- `backend/app/models/domain/meeting_note.py` — matching fields + `ChatMessage` / `ToolCall` classes

---

## 10. Plan Decomposition

This spec produces **three independently shippable implementation plans**. Each must pass a manual smoke test before the next starts, because each sits on the previous.

### Plan 1 — Variant infrastructure + bilingual editor auto-insert (B only)
- Add `ux_variant`, migration, NoteCreationModal picker, library badge.
- Add polish prompt change + structured segments persistence.
- Add `BilingualTranscriptTable` + `editorSectionBuilder`.
- Wire Save Both in variant B to insert the three editor sections.
- Verification: create a B note, record a short JA clip, click Save Both, confirm three sections appear with bilingual tables; confirm A notes still behave exactly as today.

### Plan 2 — MeetingIntelligencePanel (no chat yet)
- New sidebar branch for variant B when `summary_status === "complete"`.
- Render AI extraction group (narrative, topic fragments, delta cards, action items) in read-only form.
- Verification: on a B note that's completed the wizard, the sidebar shows the extraction panel with all wizard output; A notes unaffected.

### Plan 3 — Chat agent (within-note only)
- Add `chat_messages` and `analysis_jobs` columns + one-shot ALTER TABLE.
- Implement `chat_service.py`, `chat_tools.py`, `modules.py`, endpoints.
- Build `ChatAgent.tsx`, integrate into `MeetingIntelligencePanel`.
- Scope: the agent operates on a single note — it reads this note's transcripts + AI extraction only. Cross-note retrieval is deliberately out of scope; see Plan 4.
- Verification: ask "what are the bull/bear points?" on a B note with a transcript; agent calls `run_analysis("bull_bear")`, result appears in chat, message persists across page reload.

### Plan 4 — Cross-note embedding index + library search
- At ingest-time on Save Both: chunk the polished transcript by segment, compute embeddings via `LLMProvider.get_embeddings()`, upsert to Pinecone with metadata facets (`note_id`, `ticker`, `meeting_date`, `note_type`, `language`, `speaker`).
- Add library-search UI (extend the existing Research Panel or add a dedicated meeting-search panel) for semantic + metadata-filtered search across all meetings.
- Add two cross-note chat tools to the Plan-3 agent:
  - `search_meetings(query, filters)` — semantic search returning {note_id, timestamp, text, score}.
  - `compare_vs_previous(previous_note_id)` — already spec'd for within-note tool list; wire it to actually fetch the target note's transcripts.
- Post-MVP: decide whether to retire or keep the PostMeetingWizard fragment flow based on real usage. For now the wizard stays untouched (Q2=a).
- Verification: search across two meetings for a phrase that appears in one; confirm the hit returns with the correct timestamp and the audio link seeks to that moment.

**Current plan status (2026-04-22):** Plan 1 ✓ built + smoke-tested. Plans 2/3 next. Plan 4 sequenced after Plan 3.

---

## 11. Known Limitations

- **Editor edits don't feed back to AI source-of-truth.** Correcting a misheard word in the editor's raw transcript does not update `transcript_lines`. Deferred: "Re-sync from source" action per section.
- **Streaming chat is not in MVP.** POST /chat is synchronous; frontend shows a spinner. SSE/streaming is a v2 enhancement.
- **No A↔B toggle on a single note.** By design (see §1.4). Clone-to-B is deferred.
- **Agent context limit.** Long meetings + long chat history may blow the Gemini context on very long sessions. Mitigation: truncate `get_raw_transcript` and `get_polished_transcript` outputs to sensible limits inside the tool implementations.
- **Prior-notes loading is on-demand.** The agent must invoke `list_previous_notes_for_ticker` first; if it forgets, the user may get an unhelpful answer. Mitigation: system-prompt instruction, and (v2) hint the agent that the user's question likely needs this tool.

---

## 12. Out of Scope

- Streaming chat responses (SSE).
- Cross-note agent memory beyond explicit `compare_vs_previous` calls.
- Custom user-defined analysis modules (the per-user module library). Modules in Plan 3 are hard-coded.
- "Re-sync editor section from DB source" action.
- "Clone note as variant B" action.
- A/B analytics (which variant the user actually prefers) — add later if data would change the decision.
- Mobile layout adaptations for the 3-column tables.

---

## 13. References

- `docs/notes_recording_ux_design.md` — original design brief
- `docs/superpowers/plans/2026-04-22-recording-sidebar-phase-1.md` — completed Phase 1 plan (sidebar recording UI)
- `backend/app/interfaces/llm_provider.py` — LLMProvider port (`generate_with_tools`)
- `backend/app/adapters/llm/gemini_adapter.py` — Gemini tool-use implementation
