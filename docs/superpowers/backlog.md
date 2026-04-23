# AlphaGraph — Deferred Work Backlog

Tracks feature work that has been **designed but deferred** so it doesn't fall off the roadmap. Items here have either a spec, a plan, or a clear note of what's needed. When the user picks one up, move it from this file to active work (plan → implementation → commit).

## Meeting Intelligence — Plans 3 & 4 (paused 2026-04-23)

Spec: [`2026-04-22-meeting-intelligence-ab-design.md`](./specs/2026-04-22-meeting-intelligence-ab-design.md) §10.

### Plan 3 — Chat agent (within-note)

**Why deferred:** user wanted URL ingest + AI summary refinements first. All the prerequisites are built — chat agent is the next logical piece when the user wants it.

**What's ready:**
- `MeetingIntelligencePanel` has a "Chat agent coming soon" placeholder where the agent UI goes.
- `gemini_generate_summary` is text-only and can be invoked as an agent tool.
- Polished transcript segments are persisted in `polished_transcript_meta.segments`.
- Note metadata + transcripts persist across reloads.

**What's needed (per spec §5):**
- `chat_messages` column on `MeetingNoteORM`.
- `analysis_jobs` JSON cache column for tool results.
- `backend/app/services/chat/` package (chat_service, chat_tools, modules registry).
- Endpoints: `GET/POST/DELETE /notes/{id}/chat`.
- `ChatAgent.tsx` component inside `MeetingIntelligencePanel`.
- 7 tools: `get_user_notes`, `get_raw_transcript`, `get_polished_transcript`, `get_note_metadata`, `list_previous_notes_for_ticker`, `run_analysis(module, key_focus?)`, `compare_vs_previous(note_id)`.
- 7 analysis modules: summary / bull_bear / facts / catalysts / numbers / pecking_order / action_items.
- LLM: `GeminiAdapter.generate_with_tools` (already implemented).

**Starting point:** re-open the spec, write Plan 3 via the writing-plans skill.

### Plan 4 — Cross-note semantic search + library search

**Why deferred:** only valuable once there are several notes indexed.

**What's needed:**
- On Save Both / URL ingest completion: chunk polished transcript by segment, embed via `LLMProvider.get_embeddings()`, upsert to Pinecone with metadata facets (note_id, ticker, meeting_date, note_type, language, speaker).
- Library-search UI — extend the existing Research Panel or add a dedicated meeting-search panel.
- Wire two chat-agent cross-note tools (Plan 3 pre-req): `search_meetings(query, filters)` + make `compare_vs_previous` actually fetch the target note's transcript.

**Starting point:** decide whether to tackle after Plan 3 or as a parallel track (the embedding index doesn't depend on chat).

## Taiwan disclosure ingestion — filing types deferred past MVP

MVP covers **monthly revenue + material information** (the two Taiwan-unique data types). The following filing types are all available on MOPS (公開資訊觀測站) and should be built in subsequent sprints:

- **Quarterly financials (季報)** — Q1/Q2/Q3/Annual. Available as XBRL + PDF. Standalone + consolidated. Deadlines: May 15 / Aug 14 / Nov 14 / Mar 31.
- **Annual reports (年報)** — PDF, analogous to 10-K. Deadline: Mar 31 / before AGM.
- **Shareholders' meeting materials** — agenda, resolutions, minutes. Annual.
- **Board of directors' resolutions** — dividend proposals, capex, buybacks. Within 2 days of meeting.
- **Insider trading filings (股權異動)** — director/officer + ≥10% holder trades. Within 2–5 days.
- **Financial forecasts** — if issued; corrections required when variance >±20%.
- **Private placement / capital raise / M&A disclosures** — within 2 days of board approval.
- **Dividend distribution** — ex-div date, ratios.
- **Sustainability / ESG reports** — annual, mandatory for all listed cos since 2023.
- **Corporate governance evaluations** — FSC annual rankings.
- **Related-party transactions** — embedded in quarterly XBRL.

Additional market-data sources worth integrating after filings are covered:
- TWSE daily OHLCV + institutional flow (free JSON API)
- TPEx OTC daily data
- Central Bank of ROC (CBC) FX / interest rates
- DGBAS macro indicators

## Minor follow-ups

- **Ticker autocomplete in NoteHeaderBlock** falls back to free-form input when the universe store isn't populated. If that becomes annoying, consider fetching the universe lazily for edit sessions.
- **"Custom type" entry in the NoteHeaderBlock type dropdown** — currently only shows preset types + any existing custom type. Adding *new* custom types still requires the creation modal.
- **Frontend test harness** — no Jest/Vitest yet. Would catch bugs like the recent `setContent(..., false)` silent-swallow. Worth doing once features settle.
- **Markdown→TipTap pipeline**: URL ingest currently converts captions to segments before Gemini. If we wanted to ingest plain Markdown files (notes from elsewhere), we'd need a path that doesn't assume timestamps. Noted but not urgent.
