# Batch Folder Transcription — Design

**Date:** 2026-04-29
**Status:** Draft, pending user review.
**Side project — paused calendar-enrichment work resumes after this ships.**

## Goal

Let a user point the Notes tool at a folder of audio/video files and have the
existing transcription pipeline run over each file in turn, depositing a
`<original_name>_transcript.docx` into a `transcripts/` subfolder of that
folder. Add `.mp4` and other common video formats to the accepted-formats list
for the existing single-file upload path along the way.

The feature must work for three deployment shapes:

| Tier | When | Behavior |
|---|---|---|
| 1 | Backend on the same machine as the audio | Backend reads/writes the folder directly. Zero upload. |
| 2 | Browser-only on Chromium (Chrome/Edge/Opera) | Browser uses File System Access API to read source files and write transcripts back to the picked folder. |
| 3 | Browser-only on Firefox/Safari | Browser uploads files via existing endpoint; transcripts download to user's Downloads folder. |

## Architecture overview

Single React modal (`BatchTranscribeModal`) drives a state machine with five
states (PICK → SCAN → CONFIRM → RUNNING → DONE). On open it auto-selects a
tier; the user can override via an "Advanced" disclosure.

```
isLocal     = window.location.hostname in {'localhost', '127.0.0.1', '::1'}
hasFSAccess = 'showDirectoryPicker' in window
tier        = isLocal ? 1 : (hasFSAccess ? 2 : 3)
```

Tier 1 is the only one that needs new backend; Tier 2/3 reuse the existing
`POST /api/v1/notes/upload-transcribe` and `GET /api/v1/notes/{id}/export.docx`
endpoints, orchestrated client-side.

## Component map

### Backend (`backend/app/api/routers/v1/notes.py`)

1. **Extend `_ALLOWED_AUDIO_EXT`** to add `.mp4`, `.mov`, `.mkv`, `.avi`,
   `.m4v`. ffmpeg in the existing pipeline already extracts audio from video.
2. **New endpoint `POST /api/v1/notes/batch-transcribe-folder`** (Tier 1).
   Streams progress via Server-Sent Events.
3. **New endpoint `POST /api/v1/notes/probe-audio`** — given a file path or
   uploaded file, returns `{duration_seconds, estimated_transcribe_seconds}`
   from ffprobe. Used by the frontend to populate per-file ETA before start.
4. **Refactor docx builder** out of `GET /{note_id}/export.docx` (lines
   739-848 today) into a shared helper `_build_note_docx(note) -> bytes` that
   both the GET endpoint and the new batch endpoint can call.

### Frontend

1. **New component** `frontend/src/components/domain/notes/BatchTranscribeModal.tsx`.
2. **New "Batch folder" button** on the Notes page next to the existing
   "Upload audio" button.

## Backend specification

### Extension whitelist (one-line change)

```python
_ALLOWED_AUDIO_EXT = {
    # audio
    ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
    # video — ffmpeg pipeline extracts the audio track
    ".mp4", ".mov", ".mkv", ".avi", ".m4v",
}
```

Used by both `/upload-transcribe` and the new batch endpoint.

### `POST /api/v1/notes/batch-transcribe-folder` (Tier 1)

Auth: same as `/upload-transcribe` (reads `user_id` from auth dependency).
Tier 1 batch is **not** an authentication bypass.

Request body (JSON):

```json
{
  "folder_path": "D:/recordings/Q1-earnings",
  "translation_language": "en",
  "note_type": "earnings",
  "language": null,
  "concurrency": 2
}
```

Validation:
- `folder_path` must exist on the backend's filesystem and be a directory.
- Reject `..` segments to prevent traversal abuse via symlink.
- `concurrency` clamped to `[1, 4]`.

Response: SSE stream (`text/event-stream`), events emitted in this order:

| event | data shape | when |
|---|---|---|
| `scan_complete` | `{files: [{name, path, size_mb, duration_sec, eta_sec, status}], skipped: [{name, reason}]}` | After folder scan + ffprobe pass |
| `file_start` | `{index, name, eta_sec}` | When a file begins processing |
| `file_progress` | `{index, name, percent, stage}` (stage in {`normalizing`, `transcribing`, `translating`, `writing_doc`}) | Periodically during a file |
| `file_done` | `{index, name, transcript_path, elapsed_sec, note_id}` | When a file finishes successfully |
| `file_error` | `{index, name, error}` | When a file fails (does not abort the batch — fail-soft) |
| `batch_done` | `{total, succeeded, failed, skipped, total_elapsed_sec}` | Final event |

Folder scan logic:
1. Validate path; reject if missing or not a directory.
2. Glob audio/video files matching `_ALLOWED_AUDIO_EXT` (non-recursive — flat folder only).
3. For each file, check whether `<folder>/transcripts/<stem>_transcript.docx` exists. If so → status `"skipped"`, reason `"already_transcribed"`.
4. Sort queued files alphabetically.
5. Run ffprobe on each queued file → `duration_seconds`. ETA = `duration_seconds * 0.4 + 30s`.
6. Emit `scan_complete` with the full list (queued + skipped).

Filename collision handling:
- If `earnings.mp3` and `earnings.mp4` both exist in the folder, transcript paths are disambiguated by source extension: `earnings_mp3_transcript.docx` and `earnings_mp4_transcript.docx`.
- Detection is per-stem — collision is only triggered when two source files share a stem.

Per-file processing (same pipeline as `/upload-transcribe`):
1. ffmpeg normalize → mono 16kHz Opus (existing logic).
2. SenseVoice language detection (if `language` is null in the request).
3. `gemini_batch_transcribe_smart` (smart-chunked for >55min).
4. Optional Gemini translation per `translation_language`.
5. Write note to DB.
6. Build docx via `_build_note_docx(note)`.
7. **Tier 1 only:** write the docx bytes to `<folder>/transcripts/<stem>_transcript.docx`. Create the `transcripts/` subdir if missing.
8. Cleanup: delete the original uploaded copy and intermediate normalized opus once the docx is on disk. The `_results/` raw-Gemini-JSON safety net stays (project rule: persist paid AI output before downstream ops).

### Concurrency design

- **Default:** 2 files in parallel (asyncio semaphore in the runner).
- **User override:** `concurrency` field in request body, clamped to `[1, 4]`.
- **Auto-throttle on big files:** if any in-flight file's duration is >90 min, the runner blocks new file starts until that file finishes. Effectively drops to 1 for the duration of the big file.
- **Auto-throttle on 429:** if any chunk of any file gets a Gemini 429 response, halve the active concurrency cap for the rest of the batch (min 1).

This keeps memory worst-case at "two big files at once" not "all of them," stays well under typical Gemini TPM caps even with intra-file parallelism (the existing smart-chunker fires ~4-5 parallel chunk calls per long file), and degrades gracefully when something does hit a limit.

### `POST /api/v1/notes/probe-audio`

Request: multipart with an uploaded file, OR JSON `{path: str}` for Tier 1 mode.

Response:
```json
{
  "duration_seconds": 3492.6,
  "estimated_transcribe_seconds": 1427.0
}
```

ETA formula: `duration_seconds * 0.4 + 30`. Each batch run logs its actual ratio to `backend/data/_raw/transcribe_eta_log.jsonl` so we can refine the coefficient later.

## Frontend specification

### Tier detection (at modal open)

```ts
const isLocal     = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
const hasFSAccess = 'showDirectoryPicker' in window;
const tier        = isLocal ? 1 : (hasFSAccess ? 2 : 3);
```

Tier badge shown in the modal header:
- Tier 1: "🟢 Local mode — transcripts saved directly to folder"
- Tier 2: "🟡 Browser mode — transcripts written to picked folder"
- Tier 3: "🟠 Browser mode — transcripts download to your Downloads folder"

Advanced disclosure exposes a tier override (e.g., user on `localhost` who wants to test the browser flow).

### State machine

```
PICK    → user picks the folder
SCAN    → spinner, run probe-audio per file (Tier 2/3) or call dry-run scan (Tier 1)
CONFIRM → file list rendered, user reviews, clicks Start
RUNNING → progress UI, SSE-driven (Tier 1) or sequential awaits (Tier 2/3)
DONE    → summary card with succeeded/failed/skipped counts + "Open folder" / "Close"
```

### State 1 — PICK (tier-specific UI)

| Tier | UI |
|---|---|
| 1 | Text input for folder path + native button. Plus the tier badge. |
| 2 | Single button "Pick folder" → calls `window.showDirectoryPicker()`. Stores the returned `FileSystemDirectoryHandle` in component state for later writes. |
| 3 | Single button "Pick folder" → `<input type="file" webkitdirectory>`. Browser returns a `FileList` of read-only `File` objects. |

Translation-language picker and note-type picker reused from the existing `AudioUploadModal`.

### State 3 — CONFIRM (file list)

Visual:

```
┌─ Batch transcribe — D:\recordings\Q1-earnings ──────────────┐
│ Scanned 12 files. 9 to process, 3 already done (skipped).   │
│ Translation: [English ▾]   Type: [Earnings ▾]               │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ #  File              Size    Duration   ETA              ││
│ │ 1  TSMC_Q1.mp3       42 MB   58 min     ~25 min          ││
│ │ 2  AMAT_Q1.mp4      180 MB   1h 12m     ~32 min          ││
│ │ 3  Lam_Q1.wav      ~~~       52 min     ~22 min          ││
│ │ ✓  KLAC_Q1.mp3 (already transcribed — skipped)           ││
│ │ ...                                                      ││
│ └──────────────────────────────────────────────────────────┘│
│ Total ETA: ~3h 18m                                          │
│ ⚠ AMAT_Q1.mp4 is 4.2 GB — upload may take ~10 min           │ (Tier 2/3 only, when applicable)
│                          [Cancel]  [Start transcription]    │
└──────────────────────────────────────────────────────────────┘
```

Skipped files render with a check + grey label, no action available. Queued files render with size/duration/ETA. Big-file warning row only appears in Tier 2/3 when a file is >1 GB.

### State 4 — RUNNING (progress UI)

```
┌─ Transcribing 9 files (concurrency: 2) ──────────────────────┐
│ Overall: ████████░░░░░░░░░░░░░░░░  3/9 files  (1h 22m left) │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ ✓  1.  TSMC_Q1.mp3        Done       22 min              ││
│ │ ✓  2.  AMAT_Q1.mp4        Done       31 min              ││
│ │ ⟳  3.  Lam_Q1.wav         ████░░░░ 47%  (~12 min left)   ││
│ │                                  Stage: transcribing      ││
│ │ ⟳  4.  ApplMat_Q1.opus    █░░░░░░░ 15%  (~24 min left)   ││
│ │                                  Stage: normalizing       ││
│ │ ⏳  5.  Onsemi_Q1.mp3    Queued                          ││
│ │ ...                                                      ││
│ └──────────────────────────────────────────────────────────┘│
│                                            [Cancel batch]   │
└──────────────────────────────────────────────────────────────┘
```

Per-file row icon: ✓ done, ⟳ in flight, ⏳ queued, ✗ errored. With concurrency 2, two `⟳` rows can be active simultaneously. The header shows the active concurrency cap (which may have been auto-throttled down from 2 to 1).

**Progress fidelity by tier**:
- **Tier 1**: SSE emits `file_progress` events with both `percent` and `stage`. Per-file rows show real progress bars and stage labels (`normalizing` / `transcribing` / `translating` / `writing_doc`).
- **Tier 2/3**: existing `/upload-transcribe` is one-shot; the browser only knows "in flight" or "done." The per-file row shows an indeterminate spinner + a coarse stage label that walks `uploading → processing → writing` based on client-side milestones (upload bytes complete → response received → docx written/downloaded). No percent-within-stage. ETA still rendered from the up-front estimate, decremented as elapsed time increases.

If Tier 2/3 progress fidelity becomes a real problem in practice, a future iteration can add an SSE flavor of `/upload-transcribe`. Out of scope for v1.

### Tier-specific output writing

After a file's `file_done` event:

- **Tier 1**: backend already wrote the docx; nothing for the frontend to do.
- **Tier 2**: frontend `fetch`es `GET /api/v1/notes/{id}/export.docx`, then:
  ```ts
  const transcriptsDir = await dirHandle.getDirectoryHandle('transcripts', {create: true});
  const fileHandle = await transcriptsDir.getFileHandle(`${stem}_transcript.docx`, {create: true});
  const writable = await fileHandle.createWritable();
  await writable.write(docxBlob);
  await writable.close();
  ```
- **Tier 3**: frontend triggers a browser download (`<a download="...">` click). Lands in user's Downloads folder.

### Tier 2/3 batch loop

The browser orchestrates serially up to the concurrency cap:
- Maintain a queue of files; semaphore of size `concurrency` (default 2).
- For each file slot: upload file via `fetch` to `/upload-transcribe` (use a stream-friendly `File` body, not buffered FormData, to avoid OOM on 4GB MP4s), await response, fetch the docx, write/download it, mark done.
- Auto-throttle: if any in-flight file's duration is >90 min, hold the next start until current >90min file finishes. Implemented as a small dispatcher loop, not a fixed semaphore.
- On any `429` response from upload: halve the active concurrency, retry once with backoff.

### Cancellation

- Tier 1: client closes the SSE connection. Backend's runner detects disconnect and stops dispatching new files. Files already in-flight finish their current step, get recorded as either done or canceled.
- Tier 2/3: frontend aborts in-flight `AbortController`s and stops dispatching. Same semantics.

### Concurrent-batch guard

While a batch is RUNNING in this tab, the "Batch folder" button is disabled. Two tabs running batches concurrently is allowed — the backend has no global lock; each call is independent and shares the standard rate-limit / budget guards.

## Edge case decisions (locked-in)

| Decision | Resolution |
|---|---|
| Output extension | `.docx` (existing builder produces docx XML; `.doc` would surface a Word format-mismatch warning). |
| Filename collisions | Disambiguate by source extension. |
| Big-file warning | Show warning row in CONFIRM for any file >1 GB on Tier 2/3. |
| Subfolder scanning | Flat-folder only. Modal shows note: "Subfolders are not scanned." |
| ETA model | `duration * 0.4 + 30s`, log actual ratios for later refinement. |
| Per-file cleanup | Delete uploaded source + normalized opus after docx written. Keep `_results/` raw Gemini JSON. |
| Authentication | Same `user_id` auth as `/upload-transcribe`. |
| Concurrent batches | Per-tab disabled while running; cross-tab allowed. |
| Default concurrency | 2 |
| Auto-throttle (big files) | Drop to 1 while any >90min file is in flight. |
| Auto-throttle (429) | Halve cap, min 1. |

## Out of scope for v1

- Recursive folder scanning.
- Pause/resume mid-batch (only "Cancel" is supported).
- Resumable batch state across browser refresh (warn user not to close the tab on Tier 2/3).
- Per-file overrides for translation language or note type (whole batch shares one config).
- Mid-batch reordering.
- Custom transcript filename templates.

## Acceptance criteria

1. **Format support**: `/upload-transcribe` accepts `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v` files; ffmpeg pipeline extracts audio successfully on each.
2. **Tier 1 happy path**: paste a folder of 5 mixed-format audio files; backend writes 5 `_transcript.docx` files into `<folder>/transcripts/`; modal shows DONE state with `succeeded=5, failed=0, skipped=0`.
3. **Skip-already-done**: re-run the same batch on the same folder; modal scan reports all 5 as skipped; no Gemini calls fire.
4. **Tier 2 happy path** (Chrome/Edge): pick a folder via `showDirectoryPicker`; transcripts land in `<folder>/transcripts/`; same skip behavior.
5. **Tier 3 fallback** (Firefox/Safari): pick folder, files upload, transcripts arrive in Downloads/.
6. **Big-file safety**: 2-hour MP4 in a batch of 3 files runs to completion without OOM (verifies auto-throttle to concurrency 1 kicked in).
7. **Failure isolation**: forcibly fail file 2 (e.g., zero-byte file); files 1 and 3 still complete; modal DONE shows `failed=1, succeeded=2`.
8. **Filename collision**: folder containing `meeting.mp3` and `meeting.mp4` produces both `meeting_mp3_transcript.docx` and `meeting_mp4_transcript.docx`.
9. **Cancellation**: clicking "Cancel batch" mid-run stops dispatching new files; in-flight file finishes its current stage cleanly.
10. **Auth gate**: unauthenticated request to `/batch-transcribe-folder` returns 401.

## Testing approach

- **Backend unit**: folder-scan logic (skip detection, collision disambiguation, sort order), ETA calculator, SSE event sequencing using mocked transcription pipeline.
- **Backend integration**: real ffmpeg + real Gemini call against a tiny fixture audio file (the existing `tools/audio_recorder/recordings/` fixtures). Verify `_transcript.docx` ends up at the expected path.
- **Frontend unit**: tier detection logic across `localhost` / LAN-IP / public-URL hostnames; `'showDirectoryPicker' in window` mock; state-machine transitions; SSE event handler dispatch.
- **Frontend manual**: walk through the three tier UIs end-to-end on a 5-file fixture folder. Verify big-file warning row, skip rows, progress concurrency.
- **Failure injection**: zero-byte file, malformed mp4, oversized file (>2GB), 429-induced auto-throttle.

## File index

To create:
- `backend/app/services/notes/batch_transcribe.py` — folder-scan, runner with semaphore, SSE event emitter.
- `backend/tests/notes/test_batch_transcribe.py`
- `frontend/src/components/domain/notes/BatchTranscribeModal.tsx`
- `frontend/src/lib/api/batchTranscribeClient.ts` — SSE consumer + Tier 2/3 orchestration.
- `frontend/src/lib/api/__tests__/batchTranscribeClient.test.ts`

To modify:
- `backend/app/api/routers/v1/notes.py` — extend `_ALLOWED_AUDIO_EXT`, add the two new endpoints, refactor docx builder.
- `frontend/src/app/(dashboard)/notes/page.tsx` (or wherever the Notes page mounts the upload modal) — add "Batch folder" button.
