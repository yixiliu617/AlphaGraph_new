# Batch Folder Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MP4/video format support to single-file upload, plus a new "Batch folder" feature that processes every audio/video file in a folder and saves a `<original>_transcript.docx` per file into a `transcripts/` subfolder. Three deployment tiers (local backend, Chromium with File System Access API, Firefox/Safari fallback).

**Architecture:** Tier-1 batch runs server-side via a new SSE endpoint that streams scan + per-file progress events; Tier-2/3 batches are orchestrated client-side by reusing the existing `/upload-transcribe` endpoint per file. All three tiers share the same docx builder (refactored out of the existing `/export.docx` endpoint).

**Tech Stack:** FastAPI, Server-Sent Events (`StreamingResponse` with `text/event-stream`), python-docx, ffmpeg/ffprobe, asyncio.Semaphore, React, File System Access API (Chromium), `<input webkitdirectory>` (fallback).

**Spec:** `docs/superpowers/specs/2026-04-29-batch-folder-transcription-design.md`

---

## File structure

### Backend — files to create

| File | Responsibility |
|---|---|
| `backend/app/services/notes/__init__.py` | Empty package marker. |
| `backend/app/services/notes/docx_builder.py` | Pure function `build_note_docx(note) -> bytes`, extracted from the current inline body of `GET /notes/{note_id}/export.docx`. |
| `backend/app/services/notes/audio_probe.py` | `probe_duration_seconds(path) -> float` via ffprobe, `estimate_transcribe_seconds(duration) -> float`. |
| `backend/app/services/notes/batch_scan.py` | `scan_folder(folder_path) -> ScanResult` — globs files, detects skips and filename collisions, sorts alphabetically. |
| `backend/app/services/notes/batch_runner.py` | `run_batch(scan_result, options) -> AsyncIterator[Event]` — orchestrates per-file processing with bounded concurrency + auto-throttle. |
| `backend/tests/notes/__init__.py` | Empty package marker. |
| `backend/tests/notes/test_docx_builder.py` | Unit tests for the extracted helper. |
| `backend/tests/notes/test_audio_probe.py` | Unit tests for ffprobe wrapper + ETA formula. |
| `backend/tests/notes/test_batch_scan.py` | Unit tests for skip detection, collision disambiguation, sort. |
| `backend/tests/notes/test_batch_runner.py` | Unit tests for event sequence + concurrency + auto-throttle. |
| `backend/tests/notes/test_batch_endpoint.py` | Integration test for SSE endpoint via FastAPI TestClient. |

### Backend — files to modify

| File | What changes |
|---|---|
| `backend/app/api/routers/v1/notes.py` | Extend `_ALLOWED_AUDIO_EXT`. Replace inline body of `export_note_as_docx` with a call to the new helper. Add `POST /notes/probe-audio`. Add `POST /notes/batch-transcribe-folder`. |

### Frontend — files to create

| File | Responsibility |
|---|---|
| `frontend/src/lib/api/batchTranscribeClient.ts` | Tier detection helpers, SSE consumer for Tier 1, client-side per-file orchestration loop for Tier 2/3, FSA-API write-back, download fallback. |
| `frontend/src/components/domain/notes/BatchTranscribeModal.tsx` | Modal component implementing the PICK → SCAN → CONFIRM → RUNNING → DONE state machine. |

### Frontend — files to modify

| File | What changes |
|---|---|
| `frontend/src/components/domain/notes/AudioUploadModal.tsx` | Add MP4/video extensions to the accept list (mirror backend whitelist). |
| `frontend/src/app/(dashboard)/notes/NotesView.tsx` | Add a "Batch folder" button next to the existing "Upload Audio" button; mount `BatchTranscribeModal` when clicked; per-tab guard against concurrent batches. |

---

## Test commands

Backend (run from project root with the project's venv active):
```
PYTHONPATH=. python -m pytest backend/tests/notes/ -v
```

Specific test:
```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_scan.py::test_skips_already_transcribed -v
```

Frontend manual verification only — there is no Jest/Vitest setup in this repo. The plan calls for browser smoke tests at the end.

---

## Task 1: Backend — extend `_ALLOWED_AUDIO_EXT` to accept video formats

**Files:**
- Modify: `backend/app/api/routers/v1/notes.py:215`
- Test: `backend/tests/notes/test_allowed_extensions.py`

The existing ffmpeg pipeline in `gemini_batch_transcribe_smart` already strips video tracks when normalizing to mono 16kHz Opus, so adding the extensions to the whitelist is enough to enable MP4/MOV/etc. for the existing single-file `/upload-transcribe` endpoint. No pipeline change.

- [ ] **Step 1: Create the test file's parent dir + __init__**

```bash
mkdir -p backend/tests/notes
```

Then create `backend/tests/notes/__init__.py` (empty file).

- [ ] **Step 2: Write failing test**

Create `backend/tests/notes/test_allowed_extensions.py`:

```python
"""Verify the whitelist includes both audio and video extensions."""
from backend.app.api.routers.v1.notes import _ALLOWED_AUDIO_EXT


def test_audio_extensions_present():
    for ext in (".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm"):
        assert ext in _ALLOWED_AUDIO_EXT, f"missing audio ext {ext}"


def test_video_extensions_present():
    for ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
        assert ext in _ALLOWED_AUDIO_EXT, f"missing video ext {ext}"


def test_extensions_lowercase():
    for ext in _ALLOWED_AUDIO_EXT:
        assert ext == ext.lower(), f"non-lowercase ext {ext}"
        assert ext.startswith("."), f"ext must start with dot: {ext}"
```

- [ ] **Step 3: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_allowed_extensions.py -v
```
Expected: `test_video_extensions_present` FAILS with assertion "missing video ext .mp4".

- [ ] **Step 4: Update the whitelist**

In `backend/app/api/routers/v1/notes.py`, replace line 215:

```python
_ALLOWED_AUDIO_EXT = {
    # audio
    ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
    # video — ffmpeg pipeline extracts the audio track during normalization
    ".mp4", ".mov", ".mkv", ".avi", ".m4v",
}
```

- [ ] **Step 5: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_allowed_extensions.py -v
```
Expected: 3 PASS.

- [ ] **Step 6: Mirror the change in the frontend single-file modal**

In `frontend/src/components/domain/notes/AudioUploadModal.tsx:20`, replace:

```ts
const ACCEPTED_EXTENSIONS = [
  ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
  ".mp4", ".mov", ".mkv", ".avi", ".m4v",
];
```

- [ ] **Step 7: Commit**

```bash
git add backend/tests/notes/__init__.py backend/tests/notes/test_allowed_extensions.py backend/app/api/routers/v1/notes.py frontend/src/components/domain/notes/AudioUploadModal.tsx
git commit -m "feat(notes): accept video formats (mp4/mov/mkv/avi/m4v) in upload-transcribe"
```

---

## Task 2: Backend — extract `build_note_docx` helper

**Files:**
- Create: `backend/app/services/notes/__init__.py`
- Create: `backend/app/services/notes/docx_builder.py`
- Create: `backend/tests/notes/test_docx_builder.py`
- Modify: `backend/app/api/routers/v1/notes.py:739-862` (the `export_note_as_docx` endpoint body)

The existing endpoint inlines ~110 lines of python-docx layout code. Extract the layout into a pure function `build_note_docx(note) -> bytes` so the same logic can be called from the new batch endpoint. The endpoint becomes a thin wrapper that calls the helper, sanitizes a filename, and streams the bytes.

- [ ] **Step 1: Create the package**

Create `backend/app/services/notes/__init__.py` (empty file).

- [ ] **Step 2: Write failing test**

Create `backend/tests/notes/test_docx_builder.py`:

```python
"""Unit tests for the extracted docx-building helper."""
import io
import zipfile

import pytest
from docx import Document

from backend.app.services.notes.docx_builder import build_note_docx


class _FakeNote:
    """Minimal duck-typed stand-in for the ORM Note object the helper reads."""
    def __init__(
        self,
        *,
        note_id="abc12345-def6-7890",
        title="My Test Note",
        meeting_date=None,
        company_tickers=None,
        polished_transcript_meta=None,
    ):
        self.note_id = note_id
        self.title = title
        self.meeting_date = meeting_date
        self.company_tickers = company_tickers or []
        self.polished_transcript_meta = polished_transcript_meta or {}


def _read_docx(buf: bytes) -> Document:
    return Document(io.BytesIO(buf))


def test_returns_bytes_of_valid_docx():
    note = _FakeNote(polished_transcript_meta={
        "language": "en",
        "is_bilingual": False,
        "segments": [
            {"timestamp": "00:01", "speaker": "Alice", "text_original": "Hello world."},
        ],
        "audio_duration_sec": 60.0,
    })
    out = build_note_docx(note)
    assert isinstance(out, bytes)
    assert len(out) > 1000  # any real .docx is at least 1 KB
    # Verify it parses as a docx
    doc = _read_docx(out)
    paragraphs = [p.text for p in doc.paragraphs]
    assert any("My Test Note" in p for p in paragraphs)


def test_monolingual_renders_paragraphs_not_table():
    note = _FakeNote(polished_transcript_meta={
        "language": "en",
        "is_bilingual": False,
        "segments": [
            {"timestamp": "00:01", "speaker": "Alice", "text_original": "Hello."},
            {"timestamp": "00:05", "speaker": "Bob",   "text_original": "Goodbye."},
        ],
    })
    doc = _read_docx(build_note_docx(note))
    assert len(doc.tables) == 0
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "Hello." in body
    assert "Goodbye." in body


def test_bilingual_renders_three_column_table():
    note = _FakeNote(polished_transcript_meta={
        "language": "zh",
        "is_bilingual": True,
        "translation_label": "English",
        "segments": [
            {"timestamp": "00:01", "speaker": "A", "text_original": "你好",
             "text_english": "Hello"},
            {"timestamp": "00:05", "speaker": "B", "text_original": "再见",
             "text_english": "Goodbye"},
        ],
    })
    doc = _read_docx(build_note_docx(note))
    assert len(doc.tables) == 1
    rows = doc.tables[0].rows
    assert len(rows) == 3  # 1 header + 2 segments
    assert rows[0].cells[2].text == "English"
    assert rows[1].cells[2].text == "Hello"
    assert rows[2].cells[2].text == "Goodbye"


def test_empty_segments_raises():
    note = _FakeNote(polished_transcript_meta={"segments": []})
    with pytest.raises(ValueError, match="no polished transcript segments"):
        build_note_docx(note)
```

- [ ] **Step 3: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_docx_builder.py -v
```
Expected: ImportError because `backend.app.services.notes.docx_builder` does not exist yet.

- [ ] **Step 4: Implement the helper**

Create `backend/app/services/notes/docx_builder.py`:

```python
"""Render a Note's polished transcript to a .docx file as bytes.

Extracted from backend.app.api.routers.v1.notes.export_note_as_docx so the
batch-folder transcription path can call it directly without going through
the HTTP layer.

The function is intentionally synchronous and takes a duck-typed `note`
that exposes:
  - title, note_id (str)
  - meeting_date (str | None)
  - company_tickers (list[str])
  - polished_transcript_meta (dict)

Raises ValueError when there are no segments to render.
"""
from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.shared import Pt, Inches


def build_note_docx(note: Any) -> bytes:
    meta = note.polished_transcript_meta or {}
    segments = list(meta.get("segments") or [])
    if not segments:
        raise ValueError("Note has no polished transcript segments to render.")

    is_bilingual = bool(meta.get("is_bilingual", False))
    language     = meta.get("language") or "en"
    audio_dur    = float(meta.get("audio_duration_sec") or 0.0)
    audio_min    = round(audio_dur / 60.0, 1)

    doc = Document()
    for section in doc.sections:
        section.left_margin   = Inches(0.7)
        section.right_margin  = Inches(0.7)
        section.top_margin    = Inches(0.7)
        section.bottom_margin = Inches(0.7)

    # Title
    doc.add_heading(note.title or f"Transcript {str(note.note_id)[:8]}", level=1)

    # Metadata line
    meta_bits: list[str] = []
    if note.meeting_date:
        meta_bits.append(str(note.meeting_date))
    if audio_dur > 0:
        meta_bits.append(f"audio {audio_min} min")
    meta_bits.append(f"language {language}{'/en' if is_bilingual else ''}")
    meta_bits.append(f"{len(segments)} segments")
    if note.company_tickers:
        meta_bits.append(", ".join(note.company_tickers))
    if meta_bits:
        para = doc.add_paragraph(" · ".join(meta_bits))
        for run in para.runs:
            run.font.size = Pt(9)
            run.italic = True

    if is_bilingual:
        translation_label = meta.get("translation_label") or "English"
        table = doc.add_table(rows=1, cols=3)
        try:
            table.style = "Light Grid Accent 1"
        except KeyError:
            pass
        hdr = table.rows[0].cells
        hdr[0].text = "Time"
        hdr[1].text = "原文"  # 原文
        hdr[2].text = translation_label
        for cell in hdr:
            for run in cell.paragraphs[0].runs:
                run.bold = True
        for seg in segments:
            row = table.add_row().cells
            row[0].text = (seg.get("timestamp") or "").strip()
            speaker = (seg.get("speaker") or "").strip()
            orig    = (seg.get("text_original") or "").strip()
            row[1].text = (f"[{speaker}] {orig}" if speaker else orig)
            row[2].text = (seg.get("text_english") or "").strip()
    else:
        for seg in segments:
            ts      = (seg.get("timestamp") or "").strip()
            speaker = (seg.get("speaker") or "").strip()
            text    = (seg.get("text_original") or "").strip()
            p = doc.add_paragraph()
            ts_run = p.add_run(f"[{ts}] " if ts else "")
            ts_run.bold = True
            if speaker:
                sp_run = p.add_run(f"{speaker}: ")
                sp_run.italic = True
            p.add_run(text)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

- [ ] **Step 5: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_docx_builder.py -v
```
Expected: 4 PASS.

- [ ] **Step 6: Replace the inline body in the endpoint**

In `backend/app/api/routers/v1/notes.py`, replace the body of `export_note_as_docx` (lines 739-862, everything from `def export_note_as_docx` to the closing `return StreamingResponse(...)`) with:

```python
@router.get("/{note_id}/export.docx")
def export_note_as_docx(note_id: str, db: Session = Depends(get_db_session)):
    """Render the note's polished transcript to a Word document and stream
    it back as `<title>.docx`. Layout (title, metadata, bilingual table or
    monolingual paragraphs) is implemented in `notes.docx_builder`.
    """
    import io as _io
    import re as _re
    from fastapi.responses import StreamingResponse

    from backend.app.services.notes.docx_builder import build_note_docx

    svc = NotesService(db)
    note = svc.get_note(note_id, TENANT_ID)
    if not note:
        raise HTTPException(status_code=404, detail=f"Note {note_id} not found")

    try:
        docx_bytes = build_note_docx(note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    safe_title = _re.sub(r"[^A-Za-z0-9 \-_.()]+", "", note.title or "transcript").strip() or "transcript"
    safe_title = safe_title[:80]
    filename = f"{safe_title}.docx"

    return StreamingResponse(
        _io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 7: Smoke-test the endpoint hasn't regressed**

Start the backend (`uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`), open any existing note in the UI that has a polished transcript, hit the export button, confirm a valid .docx downloads and opens cleanly in Word.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/notes/__init__.py backend/app/services/notes/docx_builder.py backend/tests/notes/test_docx_builder.py backend/app/api/routers/v1/notes.py
git commit -m "refactor(notes): extract build_note_docx helper for reuse from batch path"
```

---

## Task 3: Backend — `audio_probe` module + `POST /notes/probe-audio` endpoint

**Files:**
- Create: `backend/app/services/notes/audio_probe.py`
- Create: `backend/tests/notes/test_audio_probe.py`
- Modify: `backend/app/api/routers/v1/notes.py` (add the endpoint)

ffprobe wrapper plus the ETA formula from the spec. The endpoint accepts either a multipart upload (Tier 2/3) OR a JSON body with a server-side `path` (Tier 1).

- [ ] **Step 1: Write failing test**

Create `backend/tests/notes/test_audio_probe.py`:

```python
"""Unit tests for ffprobe wrapper + ETA formula."""
import json
from unittest.mock import patch, MagicMock

import pytest

from backend.app.services.notes.audio_probe import (
    probe_duration_seconds,
    estimate_transcribe_seconds,
)


def _ffprobe_response(duration_sec: float) -> MagicMock:
    """Mimic the JSON shape ffprobe -show_format -of json prints."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps({"format": {"duration": str(duration_sec)}}).encode()
    proc.stderr = b""
    return proc


def test_probe_returns_duration():
    with patch(
        "backend.app.services.notes.audio_probe.subprocess.run",
        return_value=_ffprobe_response(123.45),
    ):
        assert probe_duration_seconds("/tmp/a.mp3") == pytest.approx(123.45)


def test_probe_missing_format_raises():
    bad = MagicMock()
    bad.returncode = 0
    bad.stdout = b'{"format": {}}'
    with patch("backend.app.services.notes.audio_probe.subprocess.run", return_value=bad):
        with pytest.raises(ValueError, match="duration"):
            probe_duration_seconds("/tmp/a.mp3")


def test_probe_nonzero_exit_raises():
    bad = MagicMock()
    bad.returncode = 1
    bad.stdout = b""
    bad.stderr = b"file does not exist"
    with patch("backend.app.services.notes.audio_probe.subprocess.run", return_value=bad):
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            probe_duration_seconds("/tmp/missing.mp3")


def test_eta_formula_short_audio():
    # 60s audio -> 60*0.4 + 30 = 54s
    assert estimate_transcribe_seconds(60.0) == pytest.approx(54.0)


def test_eta_formula_long_audio():
    # 1h audio = 3600s -> 3600*0.4 + 30 = 1470s
    assert estimate_transcribe_seconds(3600.0) == pytest.approx(1470.0)


def test_eta_formula_zero_audio_returns_baseline():
    assert estimate_transcribe_seconds(0.0) == pytest.approx(30.0)


def test_eta_formula_negative_input_clamped():
    # Defensive — negative duration should not produce a negative ETA.
    assert estimate_transcribe_seconds(-5.0) >= 0
```

- [ ] **Step 2: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_audio_probe.py -v
```
Expected: ImportError on the helper module.

- [ ] **Step 3: Implement the helper**

Create `backend/app/services/notes/audio_probe.py`:

```python
"""ffprobe wrapper + transcription-time ETA formula.

Used by:
  - POST /notes/probe-audio   (Tier 2/3 upload + Tier 1 path)
  - notes.batch_runner        (Tier 1 server-side scan)

ETA formula from the spec: duration_seconds * 0.4 + 30. The ratio is an
empirical guess; each batch run logs its actual ratio so we can refine it
later from data, not from speculation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


_ETA_RATIO    = 0.4
_ETA_BASELINE = 30.0   # seconds


def probe_duration_seconds(audio_path: str | Path) -> float:
    """Return the duration of an audio/video file via ffprobe."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_format", "-of", "json",
            str(audio_path),
        ],
        capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {audio_path!r}: {proc.stderr.decode(errors='replace')[:200]}"
        )
    try:
        info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        return float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"ffprobe gave no duration for {audio_path!r}: {exc}")


def estimate_transcribe_seconds(duration_seconds: float) -> float:
    """ETA = max(0, duration * 0.4) + 30. Clamps negatives to 0 baseline."""
    body = max(0.0, float(duration_seconds)) * _ETA_RATIO
    return body + _ETA_BASELINE
```

- [ ] **Step 4: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_audio_probe.py -v
```
Expected: 7 PASS.

- [ ] **Step 5: Add the FastAPI endpoint**

In `backend/app/api/routers/v1/notes.py`, immediately after the `upload_audio_and_transcribe` function (around line 432), add:

```python
class ProbeAudioPathRequest(BaseModel):
    path: str       # server-side filesystem path (Tier 1)


@router.post("/probe-audio", response_model=APIResponse)
async def probe_audio_endpoint(
    audio: Optional[UploadFile] = File(None, description="Audio/video file (Tier 2/3)"),
    path:  Optional[str]        = Form(None, description="Server-side path (Tier 1)"),
):
    """Return duration_seconds and ETA for an audio/video file.

    Two modes:
      - Multipart upload via the `audio` field   (Tier 2/3 — browser uploads each file)
      - JSON / form `path`                       (Tier 1 — backend reads from disk directly)

    The Tier-1 mode does NOT load the bytes through the HTTP body; it just
    runs ffprobe on the on-disk path. This is why the same endpoint covers
    both cases without a memory blowup on multi-GB videos.
    """
    import tempfile as _tempfile
    import uuid as _uuid

    from backend.app.services.notes.audio_probe import (
        probe_duration_seconds, estimate_transcribe_seconds,
    )

    if path:
        # Tier 1: probe server-side path directly
        from pathlib import Path as _Path
        p = _Path(path)
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail=f"Path not found or not a file: {path}")
        try:
            duration = await asyncio.to_thread(probe_duration_seconds, str(p))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    elif audio is not None and audio.filename:
        # Tier 2/3: write to a temp file, probe, delete
        ext = Path(audio.filename).suffix.lower()
        tmp = AUDIO_UPLOADS_DIR / f"_probe_{_uuid.uuid4().hex[:12]}{ext}"
        try:
            tmp.write_bytes(await audio.read())
            duration = await asyncio.to_thread(probe_duration_seconds, str(tmp))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            tmp.unlink(missing_ok=True)
    else:
        raise HTTPException(status_code=400, detail="Provide either `audio` upload or `path`.")

    eta = estimate_transcribe_seconds(duration)
    return APIResponse(
        success=True,
        data={
            "duration_seconds":             duration,
            "estimated_transcribe_seconds": eta,
        },
    )
```

- [ ] **Step 6: Smoke-test via curl**

Backend running. From a shell, with a real audio file at `D:\some\test.mp3`:

```bash
curl -X POST http://localhost:8000/api/v1/notes/probe-audio \
     -F "path=D:/some/test.mp3"
```

Expected: JSON `{"success": true, "data": {"duration_seconds": ..., "estimated_transcribe_seconds": ...}}`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/notes/audio_probe.py backend/tests/notes/test_audio_probe.py backend/app/api/routers/v1/notes.py
git commit -m "feat(notes): add probe-audio endpoint + ffprobe wrapper for ETA"
```

---

## Task 4: Backend — folder scan service

**Files:**
- Create: `backend/app/services/notes/batch_scan.py`
- Create: `backend/tests/notes/test_batch_scan.py`

`scan_folder(folder_path) -> ScanResult` does the read-only inspection: glob audio/video files, detect "already transcribed" skips, disambiguate filename collisions, run ffprobe on each queued file. The output is what the SSE endpoint emits as the `scan_complete` event.

- [ ] **Step 1: Write failing test**

Create `backend/tests/notes/test_batch_scan.py`:

```python
"""Unit tests for folder scan: skip detection, collision disambiguation, sort."""
from unittest.mock import patch
from pathlib import Path

import pytest

from backend.app.services.notes.batch_scan import (
    scan_folder, ScanResult, ScanFile, ScanSkip,
)


@pytest.fixture(autouse=True)
def _stub_probe():
    """Skip the real ffprobe call; every file in tests is "10 seconds long"."""
    with patch(
        "backend.app.services.notes.batch_scan.probe_duration_seconds",
        return_value=10.0,
    ):
        yield


def test_empty_folder(tmp_path: Path):
    result = scan_folder(str(tmp_path))
    assert result.queued == []
    assert result.skipped == []


def test_picks_up_audio_files_alphabetically(tmp_path: Path):
    (tmp_path / "z.mp3").write_bytes(b"x")
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "m.opus").write_bytes(b"x")

    result = scan_folder(str(tmp_path))
    assert [f.name for f in result.queued] == ["a.wav", "m.opus", "z.mp3"]


def test_accepts_video_extensions(tmp_path: Path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "movie.mov").write_bytes(b"x")

    result = scan_folder(str(tmp_path))
    assert {f.name for f in result.queued} == {"clip.mp4", "movie.mov"}


def test_ignores_unknown_extensions(tmp_path: Path):
    (tmp_path / "doc.txt").write_bytes(b"x")
    (tmp_path / "audio.mp3").write_bytes(b"x")

    result = scan_folder(str(tmp_path))
    assert [f.name for f in result.queued] == ["audio.mp3"]


def test_ignores_subfolder_contents(tmp_path: Path):
    (tmp_path / "top.mp3").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.mp3").write_bytes(b"x")

    result = scan_folder(str(tmp_path))
    assert [f.name for f in result.queued] == ["top.mp3"]


def test_skips_already_transcribed(tmp_path: Path):
    (tmp_path / "done.mp3").write_bytes(b"x")
    (tmp_path / "todo.mp3").write_bytes(b"x")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "done_transcript.docx").write_bytes(b"prior run")

    result = scan_folder(str(tmp_path))
    assert [f.name for f in result.queued]  == ["todo.mp3"]
    assert [s.name for s in result.skipped] == ["done.mp3"]
    assert result.skipped[0].reason == "already_transcribed"


def test_disambiguates_filename_collisions(tmp_path: Path):
    (tmp_path / "earnings.mp3").write_bytes(b"x")
    (tmp_path / "earnings.mp4").write_bytes(b"x")

    result = scan_folder(str(tmp_path))
    transcript_names = {f.transcript_name for f in result.queued}
    assert transcript_names == {"earnings_mp3_transcript.docx", "earnings_mp4_transcript.docx"}


def test_no_collision_uses_plain_transcript_name(tmp_path: Path):
    (tmp_path / "earnings.mp3").write_bytes(b"x")
    result = scan_folder(str(tmp_path))
    assert result.queued[0].transcript_name == "earnings_transcript.docx"


def test_missing_folder_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scan_folder(str(tmp_path / "does_not_exist"))


def test_path_must_be_directory(tmp_path: Path):
    p = tmp_path / "regular.mp3"
    p.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_folder(str(p))


def test_path_traversal_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="path traversal"):
        scan_folder(str(tmp_path / ".." / "escape"))
```

- [ ] **Step 2: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_scan.py -v
```
Expected: ImportError on the module.

- [ ] **Step 3: Implement the service**

Create `backend/app/services/notes/batch_scan.py`:

```python
"""Folder scan: list audio/video files, classify queued vs already-done,
disambiguate filename collisions, sort alphabetically.

Read-only — does not mutate the filesystem. Output is what the SSE
endpoint emits as the `scan_complete` event.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from backend.app.services.notes.audio_probe import (
    probe_duration_seconds,
    estimate_transcribe_seconds,
)


# Same set as backend.app.api.routers.v1.notes._ALLOWED_AUDIO_EXT, kept
# in sync manually. We don't import to avoid a circular import.
_ALLOWED_EXT = {
    ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
    ".mp4", ".mov", ".mkv", ".avi", ".m4v",
}

_TRANSCRIPTS_SUBDIR = "transcripts"


@dataclass
class ScanFile:
    name:            str       # original filename, e.g. "earnings.mp3"
    path:            str       # absolute path on disk
    size_bytes:      int
    duration_sec:    float
    eta_sec:         float
    transcript_name: str       # what we'll name the .docx (collision-aware)


@dataclass
class ScanSkip:
    name:   str
    reason: str               # "already_transcribed"


@dataclass
class ScanResult:
    folder:  str
    queued:  List[ScanFile] = field(default_factory=list)
    skipped: List[ScanSkip] = field(default_factory=list)


def _validate_folder(folder_path: str) -> Path:
    if ".." in Path(folder_path).parts:
        raise ValueError(f"path traversal not allowed: {folder_path!r}")
    p = Path(folder_path)
    if not p.exists():
        raise FileNotFoundError(f"folder does not exist: {folder_path!r}")
    if not p.is_dir():
        raise NotADirectoryError(f"path is not a directory: {folder_path!r}")
    return p


def _transcript_name(stem: str, ext: str, stem_collisions: Counter) -> str:
    """Build the transcript output name. Disambiguate when two source files
    in the same folder share the same stem (e.g. earnings.mp3 + earnings.mp4)."""
    if stem_collisions[stem] > 1:
        ext_clean = ext.lstrip(".")
        return f"{stem}_{ext_clean}_transcript.docx"
    return f"{stem}_transcript.docx"


def scan_folder(folder_path: str) -> ScanResult:
    folder = _validate_folder(folder_path)
    transcripts_dir = folder / _TRANSCRIPTS_SUBDIR

    # Flat (non-recursive) glob, filter by extension. Sort so output is
    # deterministic regardless of OS readdir order.
    candidates = sorted(
        [p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in _ALLOWED_EXT],
        key=lambda p: p.name.lower(),
    )

    # Pre-compute stem collisions (e.g. earnings.mp3 + earnings.mp4 share "earnings").
    stem_collisions: Counter = Counter(p.stem for p in candidates)

    queued:  List[ScanFile] = []
    skipped: List[ScanSkip] = []

    for p in candidates:
        ext = p.suffix.lower()
        transcript_name = _transcript_name(p.stem, ext, stem_collisions)
        if (transcripts_dir / transcript_name).exists():
            skipped.append(ScanSkip(name=p.name, reason="already_transcribed"))
            continue
        try:
            dur = probe_duration_seconds(str(p))
        except (RuntimeError, ValueError):
            # Probe failure -> still queue it; the runner records the error
            # at start time. This way scan never fails on a single bad file.
            dur = 0.0
        queued.append(ScanFile(
            name            = p.name,
            path            = str(p),
            size_bytes      = p.stat().st_size,
            duration_sec    = dur,
            eta_sec         = estimate_transcribe_seconds(dur),
            transcript_name = transcript_name,
        ))

    return ScanResult(folder=str(folder), queued=queued, skipped=skipped)
```

- [ ] **Step 4: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_scan.py -v
```
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/notes/batch_scan.py backend/tests/notes/test_batch_scan.py
git commit -m "feat(notes): folder scan with skip detection and collision disambiguation"
```

---

## Task 5: Backend — sequential batch runner

**Files:**
- Create: `backend/app/services/notes/batch_runner.py`
- Create: `backend/tests/notes/test_batch_runner.py`

The runner is an `async` generator that yields a stream of typed events. Sequential first (concurrency=1); Task 6 adds bounded concurrency.

Per-file pipeline:
1. `file_start`
2. ffmpeg + Gemini transcription via existing `gemini_batch_transcribe_smart`
3. Build .docx via `build_note_docx`
4. **Tier 1 only:** write to `<folder>/transcripts/<transcript_name>`
5. Save note to DB
6. `file_done` (or `file_error` on exception — the loop continues)

The runner takes a `transcribe_fn` parameter so tests can inject a fake transcription pipeline.

- [ ] **Step 1: Write failing test**

Create `backend/tests/notes/test_batch_runner.py`:

```python
"""Unit tests for the per-file batch runner.

We don't exercise the real Gemini pipeline -- we inject a stub
`transcribe_fn` that simulates the result so tests stay fast and
deterministic.
"""
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.app.services.notes.batch_scan import ScanResult, ScanFile, ScanSkip
from backend.app.services.notes.batch_runner import (
    BatchOptions, run_batch, EventKind,
)


def _make_scan(tmp_path: Path, *names: str) -> ScanResult:
    queued = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"\0" * 1024)
        queued.append(ScanFile(
            name=n, path=str(p), size_bytes=1024,
            duration_sec=10.0, eta_sec=34.0,
            transcript_name=Path(n).stem + "_transcript.docx",
        ))
    return ScanResult(folder=str(tmp_path), queued=queued, skipped=[])


def _fake_transcribe_ok(path, lang, _glossary, translation):
    """Mimic gemini_batch_transcribe_smart's success shape."""
    return {
        "text": "hello world",
        "segments": [{"timestamp": "00:01", "speaker": "", "text_original": "Hi"}],
        "language": lang or "en",
        "is_bilingual": False,
        "translation_label": "English",
        "audio_duration_sec": 10.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "gemini_seconds": 1.0,
        "total_seconds": 1.5,
        "chunk_count": 1,
        "chunk_seconds": [10.0],
        "key_topics": [],
    }


async def _drain(agen):
    """Collect all events from the async generator."""
    return [e async for e in agen]


@pytest.mark.asyncio
async def test_emits_scan_complete_first(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    fake_save = MagicMock(return_value=MagicMock(note_id="note-1"))
    events = await _drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=fake_save))
    assert events[0].kind == EventKind.SCAN_COMPLETE
    assert events[0].data["queued_count"] == 1


@pytest.mark.asyncio
async def test_emits_file_start_and_done_per_file(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    fake_save = MagicMock(return_value=MagicMock(note_id="note-1"))
    events = await _drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=fake_save))
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.FILE_START) == 2
    assert kinds.count(EventKind.FILE_DONE)  == 2
    assert kinds[-1] == EventKind.BATCH_DONE


@pytest.mark.asyncio
async def test_writes_docx_to_transcripts_subdir(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    fake_save = MagicMock(return_value=MagicMock(note_id="note-1"))
    await _drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=fake_save))
    out = tmp_path / "transcripts" / "a_transcript.docx"
    assert out.exists()
    assert out.stat().st_size > 1000  # any real docx is > 1KB


@pytest.mark.asyncio
async def test_failure_in_one_file_does_not_abort_batch(tmp_path):
    scan = _make_scan(tmp_path, "good.mp3", "bad.mp3", "good2.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    fake_save = MagicMock(return_value=MagicMock(note_id="note-1"))

    def flaky(path, lang, _g, t):
        if "bad" in path:
            raise RuntimeError("simulated transcription failure")
        return _fake_transcribe_ok(path, lang, _g, t)

    events = await _drain(run_batch(scan, options, transcribe_fn=flaky, save_note_fn=fake_save))
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.FILE_DONE)  == 2
    assert kinds.count(EventKind.FILE_ERROR) == 1
    assert kinds[-1] == EventKind.BATCH_DONE
    final = events[-1].data
    assert final["succeeded"] == 2
    assert final["failed"]    == 1


@pytest.mark.asyncio
async def test_batch_done_includes_skipped_count(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    scan.skipped.append(ScanSkip(name="b.mp3", reason="already_transcribed"))
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    fake_save = MagicMock(return_value=MagicMock(note_id="note-1"))
    events = await _drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=fake_save))
    final = events[-1]
    assert final.kind == EventKind.BATCH_DONE
    assert final.data["skipped"] == 1
```

- [ ] **Step 2: Verify pytest-asyncio is available**

```
PYTHONPATH=. python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"
```
Expected: prints a version number. If it fails, install: `pip install pytest-asyncio`. Then add `asyncio_mode = "auto"` to `pyproject.toml` or `pytest.ini` if not already configured. (Check: `grep -n asyncio_mode pyproject.toml pytest.ini setup.cfg` — if anything matches, the config exists. The `@pytest.mark.asyncio` decorator works in either mode.)

- [ ] **Step 3: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_runner.py -v
```
Expected: ImportError on the runner module.

- [ ] **Step 4: Implement the runner**

Create `backend/app/services/notes/batch_runner.py`:

```python
"""Per-file batch runner.

`run_batch` is an async generator that yields a typed stream of events:
    SCAN_COMPLETE -> FILE_START -> FILE_PROGRESS* -> (FILE_DONE | FILE_ERROR) -> ... -> BATCH_DONE

The HTTP layer maps each event to an SSE message; tests consume the
generator directly. Failures in a single file do not abort the batch --
the loop yields FILE_ERROR and continues with the next file.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from backend.app.services.notes.batch_scan import ScanResult, ScanFile
from backend.app.services.notes.docx_builder import build_note_docx


class EventKind(str, Enum):
    SCAN_COMPLETE = "scan_complete"
    FILE_START    = "file_start"
    FILE_PROGRESS = "file_progress"
    FILE_DONE     = "file_done"
    FILE_ERROR    = "file_error"
    BATCH_DONE    = "batch_done"


@dataclass
class Event:
    kind: EventKind
    data: dict


@dataclass
class BatchOptions:
    translation_language: str          # "en", "zh-hans", ..., or any free-form language name
    note_type:            str          # "meeting_transcript" / "earnings_call" / etc.
    language:             Optional[str]   # source language override; None = auto-detect
    concurrency:          int = 2


# Type alias: a transcribe function with the same signature as
# backend.app.services.live_transcription.gemini_batch_transcribe_smart.
TranscribeFn = Callable[[str, Optional[str], str, str], dict]


async def run_batch(
    scan:          ScanResult,
    options:       BatchOptions,
    transcribe_fn: TranscribeFn,
    save_note_fn:  Callable[[ScanFile, dict, BatchOptions], Any],
) -> AsyncIterator[Event]:
    """Yield events for an entire batch.

    Args:
      scan:          ScanResult from batch_scan.scan_folder
      options:       BatchOptions
      transcribe_fn: callable(path, lang, glossary, translation_lang) -> dict
                     (matches gemini_batch_transcribe_smart's signature)
      save_note_fn:  callable(scan_file, transcribe_result, options) -> note
                     The runner uses note.note_id for reporting.
    """
    yield Event(EventKind.SCAN_COMPLETE, {
        "folder":        scan.folder,
        "queued_count":  len(scan.queued),
        "skipped_count": len(scan.skipped),
        "queued":        [_pack_scan_file(f) for f in scan.queued],
        "skipped":       [{"name": s.name, "reason": s.reason} for s in scan.skipped],
    })

    succeeded = 0
    failed    = 0
    started   = time.monotonic()

    for index, sf in enumerate(scan.queued):
        async for ev in _process_one(index, sf, options, transcribe_fn, save_note_fn):
            yield ev
            if ev.kind == EventKind.FILE_DONE:
                succeeded += 1
            elif ev.kind == EventKind.FILE_ERROR:
                failed += 1

    yield Event(EventKind.BATCH_DONE, {
        "total":             len(scan.queued),
        "succeeded":         succeeded,
        "failed":            failed,
        "skipped":           len(scan.skipped),
        "total_elapsed_sec": time.monotonic() - started,
    })


def _pack_scan_file(f: ScanFile) -> dict:
    return {
        "name":            f.name,
        "path":            f.path,
        "size_mb":         round(f.size_bytes / (1024 * 1024), 2),
        "duration_sec":    f.duration_sec,
        "eta_sec":         f.eta_sec,
        "transcript_name": f.transcript_name,
        "status":          "queued",
    }


async def _process_one(
    index:         int,
    sf:            ScanFile,
    options:       BatchOptions,
    transcribe_fn: TranscribeFn,
    save_note_fn:  Callable,
) -> AsyncIterator[Event]:
    yield Event(EventKind.FILE_START, {
        "index":   index,
        "name":    sf.name,
        "eta_sec": sf.eta_sec,
    })
    file_started = time.monotonic()

    try:
        # Stage: transcription. The existing pipeline blocks the calling
        # thread, so we run it in the default executor to keep the event
        # loop responsive (other generators / SSE writers).
        yield Event(EventKind.FILE_PROGRESS, {
            "index": index, "name": sf.name, "percent": 5, "stage": "normalizing",
        })
        transcribe_result = await asyncio.to_thread(
            transcribe_fn, sf.path, options.language, "", options.translation_language,
        )
        if transcribe_result.get("error"):
            raise RuntimeError(f"transcription error: {transcribe_result['error']}")

        yield Event(EventKind.FILE_PROGRESS, {
            "index": index, "name": sf.name, "percent": 70, "stage": "writing_doc",
        })

        # Persist the note (caller-provided to keep the runner DB-agnostic
        # for tests).
        note = save_note_fn(sf, transcribe_result, options)

        # Write the .docx to <folder>/transcripts/<transcript_name>
        docx_bytes = build_note_docx(note)
        out_path = Path(sf.path).parent / "transcripts" / sf.transcript_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(docx_bytes)

        yield Event(EventKind.FILE_DONE, {
            "index":           index,
            "name":            sf.name,
            "transcript_path": str(out_path),
            "elapsed_sec":     time.monotonic() - file_started,
            "note_id":         getattr(note, "note_id", None),
        })
    except Exception as exc:    # noqa: BLE001 -- intentional: report and continue
        yield Event(EventKind.FILE_ERROR, {
            "index": index,
            "name":  sf.name,
            "error": f"{type(exc).__name__}: {exc}",
        })
```

- [ ] **Step 5: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_runner.py -v
```
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/notes/batch_runner.py backend/tests/notes/test_batch_runner.py
git commit -m "feat(notes): sequential batch runner with typed event stream"
```

---

## Task 6: Backend — bounded concurrency + auto-throttle

**Files:**
- Modify: `backend/app/services/notes/batch_runner.py`
- Modify: `backend/tests/notes/test_batch_runner.py` (add concurrency tests)

Add two policies:
1. **Default cap** of 2 concurrent files (asyncio.Semaphore).
2. **Big-file throttle**: if any in-flight file's `duration_sec > 90 * 60`, the runner blocks new starts until that file finishes (effectively cap=1 for the duration of any big file).
3. **429 throttle**: if the transcription function raises an exception whose stringified form contains "429" or "rate", halve the active cap (min 1) for the rest of the batch.

- [ ] **Step 1: Add tests for concurrency**

Append to `backend/tests/notes/test_batch_runner.py`:

```python
@pytest.mark.asyncio
async def test_concurrency_two_runs_two_at_once(tmp_path):
    """With concurrency=2 and 2 short files, both should be in flight at the same time."""
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3", "c.mp3", "d.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=2)
    fake_save = MagicMock(return_value=MagicMock(note_id="x"))

    inflight_max = {"v": 0}
    inflight_now = {"v": 0}

    async def slow_fake(path, lang, _g, t):
        inflight_now["v"] += 1
        inflight_max["v"] = max(inflight_max["v"], inflight_now["v"])
        await asyncio.sleep(0.05)
        inflight_now["v"] -= 1
        return _fake_transcribe_ok(path, lang, _g, t)

    def sync_fake(path, lang, _g, t):
        return asyncio.run(slow_fake(path, lang, _g, t))

    # Patch run_batch's executor wrap — easier: pass an async-aware wrapper directly
    with patch.object(asyncio, "to_thread", new=lambda fn, *a, **kw: fn(*a, **kw)):
        with patch("backend.app.services.notes.batch_runner.asyncio.to_thread",
                   new=lambda fn, *a, **kw: slow_fake(*a, **kw)):
            await _drain(run_batch(scan, options,
                                    transcribe_fn=_fake_transcribe_ok,
                                    save_note_fn=fake_save))

    assert inflight_max["v"] >= 2, f"expected >=2 in flight, saw {inflight_max['v']}"


@pytest.mark.asyncio
async def test_big_file_drops_concurrency_to_one(tmp_path):
    """A file with duration_sec > 90*60 forces the runner to cap at 1 while it's in flight."""
    scan = _make_scan(tmp_path, "small.mp3", "huge.mp3", "small2.mp3")
    # Override the huge one's duration in place
    for f in scan.queued:
        if f.name == "huge.mp3":
            f.duration_sec = 95 * 60   # > 90 min threshold
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=2)
    fake_save = MagicMock(return_value=MagicMock(note_id="x"))

    inflight_during_huge = {"max": 0, "now": 0}

    async def fake(path, lang, _g, t):
        inflight_during_huge["now"] += 1
        if "huge" in path:
            # While the huge one is in flight, capture the max simultaneous count.
            inflight_during_huge["max"] = max(
                inflight_during_huge["max"], inflight_during_huge["now"],
            )
            await asyncio.sleep(0.05)
        inflight_during_huge["now"] -= 1
        return _fake_transcribe_ok(path, lang, _g, t)

    with patch("backend.app.services.notes.batch_runner.asyncio.to_thread",
               new=lambda fn, *a, **kw: fake(*a, **kw)):
        await _drain(run_batch(scan, options,
                                transcribe_fn=_fake_transcribe_ok,
                                save_note_fn=fake_save))

    assert inflight_during_huge["max"] == 1, (
        f"expected huge file to run alone, saw {inflight_during_huge['max']} concurrent"
    )


@pytest.mark.asyncio
async def test_429_halves_concurrency_for_remaining_files(tmp_path):
    """A '429' error on file 1 halves the cap; remaining files run with the lower cap."""
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3", "c.mp3", "d.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=4)
    fake_save = MagicMock(return_value=MagicMock(note_id="x"))

    inflight = {"now": 0, "max_after_429": 0}
    saw_429 = {"v": False}

    async def fake(path, lang, _g, t):
        # First file errors with a 429-flavored message.
        if "a.mp3" in path:
            raise RuntimeError("HTTP 429 too many requests")
        inflight["now"] += 1
        if saw_429["v"]:
            inflight["max_after_429"] = max(inflight["max_after_429"], inflight["now"])
        await asyncio.sleep(0.03)
        inflight["now"] -= 1
        return _fake_transcribe_ok(path, lang, _g, t)

    # Hook to flip saw_429 once we observe the file_error event.
    async def _drain_with_flag(agen):
        out = []
        async for ev in agen:
            if ev.kind == EventKind.FILE_ERROR and "429" in ev.data["error"]:
                saw_429["v"] = True
            out.append(ev)
        return out

    with patch("backend.app.services.notes.batch_runner.asyncio.to_thread",
               new=lambda fn, *a, **kw: fake(*a, **kw)):
        await _drain_with_flag(run_batch(scan, options,
                                          transcribe_fn=_fake_transcribe_ok,
                                          save_note_fn=fake_save))

    # Cap was 4; after 429 it should drop to 2 -> max simultaneous in flight <= 2.
    assert inflight["max_after_429"] <= 2, (
        f"expected cap halved after 429, saw {inflight['max_after_429']}"
    )
```

- [ ] **Step 2: Run tests to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_runner.py -v
```
Expected: the three new tests fail; existing 5 still pass. The new tests fail because the runner currently runs one-at-a-time without any concurrency or throttling logic.

- [ ] **Step 3: Update runner with concurrency + auto-throttle**

In `backend/app/services/notes/batch_runner.py`, replace the body of `run_batch` and `_process_one` with the version below (helpers `_pack_scan_file`, `EventKind`, `Event`, `BatchOptions` stay the same):

```python
async def run_batch(
    scan:          ScanResult,
    options:       BatchOptions,
    transcribe_fn: TranscribeFn,
    save_note_fn:  Callable[[ScanFile, dict, BatchOptions], Any],
) -> AsyncIterator[Event]:
    """Yield events for an entire batch with bounded concurrency + auto-throttle.

    Concurrency policy:
      - cap starts at options.concurrency (clamped to [1, 4])
      - if any in-flight file has duration_sec > BIG_FILE_SECONDS, the cap
        is effectively 1 (no new file starts until the big one finishes)
      - if any file errors with a 429-flavored message, cap halves (min 1)
        for the rest of the batch
    """
    BIG_FILE_SECONDS = 90 * 60       # 90 minutes
    cap = max(1, min(4, options.concurrency))

    yield Event(EventKind.SCAN_COMPLETE, {
        "folder":        scan.folder,
        "queued_count":  len(scan.queued),
        "skipped_count": len(scan.skipped),
        "queued":        [_pack_scan_file(f) for f in scan.queued],
        "skipped":       [{"name": s.name, "reason": s.reason} for s in scan.skipped],
    })

    succeeded = 0
    failed    = 0
    started   = time.monotonic()

    out_q: asyncio.Queue[Optional[Event]] = asyncio.Queue()
    inflight_durations: list[float] = []     # mutable shared state
    cap_box = {"value": cap}                 # mutable so 429-throttle can shrink it
    inflight_lock = asyncio.Lock()

    async def slot_available() -> bool:
        async with inflight_lock:
            if len(inflight_durations) >= cap_box["value"]:
                return False
            if any(d > BIG_FILE_SECONDS for d in inflight_durations):
                return False
            return True

    async def worker(index: int, sf: ScanFile):
        async with inflight_lock:
            inflight_durations.append(sf.duration_sec)
        try:
            async for ev in _process_one(index, sf, options, transcribe_fn, save_note_fn):
                await out_q.put(ev)
                # Auto-throttle on 429
                if ev.kind == EventKind.FILE_ERROR:
                    err = (ev.data.get("error") or "").lower()
                    if "429" in err or "rate" in err:
                        async with inflight_lock:
                            cap_box["value"] = max(1, cap_box["value"] // 2)
        finally:
            async with inflight_lock:
                inflight_durations.remove(sf.duration_sec)

    async def dispatcher():
        tasks: list[asyncio.Task] = []
        for index, sf in enumerate(scan.queued):
            # Wait for a slot. We poll on a short interval rather than
            # a condition variable to keep the code simple; in practice
            # files take seconds to minutes, so the 50ms poll is noise.
            while not await slot_available():
                await asyncio.sleep(0.05)
            tasks.append(asyncio.create_task(worker(index, sf)))
        # Wait for all in-flight workers to finish before we close the queue.
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await out_q.put(None)   # sentinel: dispatcher done

    dispatcher_task = asyncio.create_task(dispatcher())

    while True:
        ev = await out_q.get()
        if ev is None:
            break
        yield ev
        if ev.kind == EventKind.FILE_DONE:
            succeeded += 1
        elif ev.kind == EventKind.FILE_ERROR:
            failed += 1

    await dispatcher_task

    yield Event(EventKind.BATCH_DONE, {
        "total":             len(scan.queued),
        "succeeded":         succeeded,
        "failed":            failed,
        "skipped":           len(scan.skipped),
        "total_elapsed_sec": time.monotonic() - started,
    })
```

`_process_one` stays unchanged from Task 5.

- [ ] **Step 4: Run tests to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_runner.py -v
```
Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/notes/batch_runner.py backend/tests/notes/test_batch_runner.py
git commit -m "feat(notes): bounded-2 concurrency + auto-throttle for big files / 429s"
```

---

## Task 7: Backend — `POST /notes/batch-transcribe-folder` SSE endpoint

**Files:**
- Modify: `backend/app/api/routers/v1/notes.py` (add the endpoint + a SSE adapter)
- Create: `backend/tests/notes/test_batch_endpoint.py`

The endpoint:
1. Validates `folder_path` (exists, is dir, no `..`).
2. Calls `scan_folder`.
3. Streams the runner's events as SSE messages (`event: <kind>\ndata: <json>\n\n`).
4. Wires `transcribe_fn` to the real `gemini_batch_transcribe_smart` and `save_note_fn` to the existing NotesService save logic.

- [ ] **Step 1: Write failing test**

Create `backend/tests/notes/test_batch_endpoint.py`:

```python
"""Integration test for POST /api/v1/notes/batch-transcribe-folder.

We patch the transcription function and DB save to keep this test fast
and deterministic. The goal is to exercise the SSE wiring, not the
underlying pipeline (which is covered by the runner unit tests).
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from backend.main import app


def _fake_transcribe_ok(path, lang, _glossary, translation):
    return {
        "text": "x",
        "segments": [{"timestamp": "00:01", "speaker": "", "text_original": "Hi"}],
        "language": lang or "en",
        "is_bilingual": False,
        "translation_label": "English",
        "audio_duration_sec": 10.0,
        "input_tokens": 100, "output_tokens": 50,
        "gemini_seconds": 1.0, "total_seconds": 1.5,
        "chunk_count": 1, "chunk_seconds": [10.0],
        "key_topics": [],
    }


def _parse_sse(text: str) -> list[tuple[str, str]]:
    """Cheap SSE parser: returns [(event, data_json), ...]."""
    out, ev, dat = [], None, []
    for line in text.splitlines():
        if not line:
            if ev is not None:
                out.append((ev, "\n".join(dat)))
                ev, dat = None, []
            continue
        if line.startswith("event: "):
            ev = line[len("event: "):]
        elif line.startswith("data: "):
            dat.append(line[len("data: "):])
    return out


def test_batch_endpoint_returns_sse_stream(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"\0" * 1024)

    with patch(
        "backend.app.services.notes.batch_scan.probe_duration_seconds",
        return_value=10.0,
    ), patch(
        "backend.app.api.routers.v1.notes.gemini_batch_transcribe_smart",
        new=_fake_transcribe_ok,
    ):
        client = TestClient(app)
        resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
            "folder_path":          str(tmp_path),
            "translation_language": "en",
            "note_type":            "meeting_transcript",
            "language":             None,
            "concurrency":          1,
        })
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        kinds = [k for (k, _) in events]
        assert "scan_complete" in kinds
        assert "file_start"    in kinds
        assert "file_done"     in kinds
        assert kinds[-1] == "batch_done"


def test_batch_endpoint_404_on_missing_folder(tmp_path):
    client = TestClient(app)
    resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
        "folder_path":          str(tmp_path / "does_not_exist"),
        "translation_language": "en",
        "note_type":            "meeting_transcript",
        "language":             None,
        "concurrency":          1,
    })
    assert resp.status_code == 404


def test_batch_endpoint_400_on_path_traversal():
    client = TestClient(app)
    resp = client.post("/api/v1/notes/batch-transcribe-folder", json={
        "folder_path":          "/tmp/../etc",
        "translation_language": "en",
        "note_type":            "meeting_transcript",
        "language":             None,
        "concurrency":          1,
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify failure**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_endpoint.py -v
```
Expected: 404 from FastAPI on the unknown route.

- [ ] **Step 3: Add the endpoint**

In `backend/app/api/routers/v1/notes.py`, immediately after the new `probe_audio_endpoint` (Task 3), add:

```python
class BatchTranscribeRequest(BaseModel):
    folder_path:          str
    translation_language: str = "en"
    note_type:            str = "meeting_transcript"
    language:             Optional[str] = None
    concurrency:          int = 2


@router.post("/batch-transcribe-folder")
async def batch_transcribe_folder(
    request: BatchTranscribeRequest,
    db:      Session = Depends(get_db_session),
):
    """Tier-1 batch transcription. Streams progress via Server-Sent Events.

    Folder is read on the *backend's* filesystem -- meaningful only when
    backend and user are on the same machine. Output: one .docx per
    audio/video file, written to <folder>/transcripts/<stem>_transcript.docx.
    """
    import json as _json
    from datetime import datetime as _dt
    from fastapi.responses import StreamingResponse

    from backend.app.services.live_transcription import gemini_batch_transcribe_smart
    from backend.app.services.editor_doc_builder import build_editor_doc_from_polish_meta
    from backend.app.services.notes.batch_scan import scan_folder
    from backend.app.services.notes.batch_runner import (
        run_batch, BatchOptions,
    )

    # 1. Validate folder + scan
    try:
        scan = await asyncio.to_thread(scan_folder, request.folder_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    options = BatchOptions(
        translation_language = request.translation_language,
        note_type            = request.note_type,
        language             = request.language,
        concurrency          = request.concurrency,
    )

    # 2. Closure: persist a finished file as a Note in the DB. The runner
    #    calls this synchronously; the DB session is the per-request one.
    svc = NotesService(db)

    def _save_note(sf, transcribe_result, opts) -> Any:
        derived_title = Path(sf.name).stem or sf.name
        note = svc.create_note(
            tenant_id      = TENANT_ID,
            title          = derived_title,
            note_type      = opts.note_type,
            company_tickers=[],
        )
        translation_label = transcribe_result.get("translation_label") or "English"
        editor_doc = build_editor_doc_from_polish_meta(
            segments         = transcribe_result.get("segments") or [],
            summary          = {},
            is_bilingual     = transcribe_result.get("is_bilingual", False),
            raw_lines        = None,
            translation_label= translation_label,
        )
        svc.update_note(
            note.note_id, TENANT_ID,
            recording_path = sf.name,         # source filename only
            editor_content = editor_doc,
        )
        svc.save_polished_transcript(
            note_id    = note.note_id,
            tenant_id  = TENANT_ID,
            markdown   = transcribe_result.get("text", ""),
            language   = transcribe_result.get("language") or opts.language or "en",
            meta={
                "input_tokens":     transcribe_result.get("input_tokens", 0),
                "output_tokens":    transcribe_result.get("output_tokens", 0),
                "model":            "gemini-2.5-flash",
                "ran_at":           _dt.utcnow().isoformat(),
                "is_bilingual":     transcribe_result.get("is_bilingual", False),
                "key_topics":       transcribe_result.get("key_topics", []),
                "segments":         transcribe_result.get("segments") or [],
                "summary":          {},
                "source":           "batch_folder",
                "uploaded_filename":sf.name,
                "translation_language": opts.translation_language,
                "translation_label":    translation_label,
                "gemini_seconds":   transcribe_result.get("gemini_seconds"),
                "total_seconds":    transcribe_result.get("total_seconds"),
                "audio_duration_sec": transcribe_result.get("audio_duration_sec"),
                "chunk_count":      transcribe_result.get("chunk_count", 1),
                "chunk_seconds":    transcribe_result.get("chunk_seconds", []),
            },
        )
        # Return the freshly-loaded note so the runner can hand it to build_note_docx.
        return svc.get_note(note.note_id, TENANT_ID)

    # 3. SSE adapter -- map each Event to text/event-stream frames
    async def event_stream():
        try:
            async for ev in run_batch(
                scan, options,
                transcribe_fn = gemini_batch_transcribe_smart,
                save_note_fn  = _save_note,
            ):
                payload = _json.dumps(ev.data, default=str)
                yield f"event: {ev.kind.value}\ndata: {payload}\n\n".encode("utf-8")
        except Exception as exc:    # noqa: BLE001
            err = _json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            yield f"event: batch_error\ndata: {err}\n\n".encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disables nginx response buffering
        },
    )
```

- [ ] **Step 4: Run test to verify pass**

```
PYTHONPATH=. python -m pytest backend/tests/notes/test_batch_endpoint.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Run the entire notes test directory to check regression**

```
PYTHONPATH=. python -m pytest backend/tests/notes/ -v
```
Expected: every test in the directory PASSES (Tasks 1-7 combined: ~25 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routers/v1/notes.py backend/tests/notes/test_batch_endpoint.py
git commit -m "feat(notes): POST /batch-transcribe-folder SSE endpoint (Tier 1)"
```

---

## Task 8: Frontend — `batchTranscribeClient.ts` (tier detection + SSE consumer + Tier 2/3 orchestration)

**Files:**
- Create: `frontend/src/lib/api/batchTranscribeClient.ts`

This module is pure logic — no JSX. The modal in Task 9 imports from it.

- [ ] **Step 1: Implement the client module**

Create `frontend/src/lib/api/batchTranscribeClient.ts`:

```typescript
/**
 * batchTranscribeClient
 *
 * Tier detection, SSE consumption (Tier 1), per-file orchestration loop
 * for Tier 2/3, and the per-tier output write-back logic.
 *
 *   Tier 1 (local backend): backend writes transcripts to disk directly.
 *   Tier 2 (Chromium):      browser writes via File System Access API.
 *   Tier 3 (FF/Safari):     browser triggers a download per file.
 */

import { notesClient } from "./notesClient";

export type Tier = 1 | 2 | 3;

export interface ScanFile {
  name:            string;
  path:            string;        // absolute on backend (Tier 1) OR webkitRelativePath (Tier 2/3)
  size_mb:         number;
  duration_sec:    number;
  eta_sec:         number;
  transcript_name: string;
  status:          "queued" | "in_flight" | "done" | "error";
}

export interface ScanSkip { name: string; reason: string; }

export interface ScanComplete {
  folder:        string;
  queued_count:  number;
  skipped_count: number;
  queued:        ScanFile[];
  skipped:       ScanSkip[];
}

export type BatchEvent =
  | { kind: "scan_complete"; data: ScanComplete }
  | { kind: "file_start";    data: { index: number; name: string; eta_sec: number } }
  | { kind: "file_progress"; data: { index: number; name: string; percent: number; stage: string } }
  | { kind: "file_done";     data: { index: number; name: string; transcript_path: string; elapsed_sec: number; note_id: string } }
  | { kind: "file_error";    data: { index: number; name: string; error: string } }
  | { kind: "batch_done";    data: { total: number; succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } }
  | { kind: "batch_error";   data: { error: string } };

// ---------------------------------------------------------------------------
// Tier detection
// ---------------------------------------------------------------------------

export function detectTier(): Tier {
  if (typeof window === "undefined") return 3;
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") return 1;
  if ("showDirectoryPicker" in window) return 2;
  return 3;
}

export function tierLabel(tier: Tier): string {
  if (tier === 1) return "Local mode -- transcripts saved directly to folder";
  if (tier === 2) return "Browser mode -- transcripts written to picked folder";
  return            "Browser mode -- transcripts download to your Downloads folder";
}

// ---------------------------------------------------------------------------
// Tier 1: SSE consumer
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

interface RunBatchTier1Args {
  folder_path:          string;
  translation_language: string;
  note_type:            string;
  language:             string | null;
  concurrency:          number;
  signal?:              AbortSignal;
  onEvent:              (ev: BatchEvent) => void;
}

export async function runBatchTier1(args: RunBatchTier1Args): Promise<void> {
  const resp = await fetch(`${API_BASE}/notes/batch-transcribe-folder`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      folder_path:          args.folder_path,
      translation_language: args.translation_language,
      note_type:            args.note_type,
      language:             args.language,
      concurrency:          args.concurrency,
    }),
    signal:  args.signal,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Batch endpoint ${resp.status}: ${text.slice(0, 300)}`);
  }
  if (!resp.body) throw new Error("Batch endpoint returned no body");

  // Manual SSE parser. Lightweight; we don't need EventSource because we
  // POST a body, which EventSource doesn't support.
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx = buf.indexOf("\n\n");
    while (idx !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = parseSseFrame(chunk);
      if (ev) args.onEvent(ev);
      idx = buf.indexOf("\n\n");
    }
  }
}

function parseSseFrame(text: string): BatchEvent | null {
  let kind = "";
  const dataLines: string[] = [];
  for (const line of text.split("\n")) {
    if (line.startsWith("event: ")) kind = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!kind || dataLines.length === 0) return null;
  try {
    const data = JSON.parse(dataLines.join("\n"));
    return { kind, data } as BatchEvent;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Tier 2/3: client-side orchestration loop
// ---------------------------------------------------------------------------

interface RunBatchClientArgs {
  files:                File[];                       // already filtered (allowed extensions)
  dirHandle?:           FileSystemDirectoryHandle;    // Tier 2 only
  translation_language: string;
  note_type:            string;
  language:             string | null;
  concurrency:          number;
  signal?:              AbortSignal;
  onEvent:              (ev: BatchEvent) => void;
  /** Probed at scan time -- map filename -> {duration_sec, eta_sec}. */
  probedDurations:      Map<string, { duration_sec: number; eta_sec: number; transcript_name: string }>;
}

export async function runBatchClient(args: RunBatchClientArgs): Promise<void> {
  const BIG_FILE_SECONDS = 90 * 60;
  const startCap = Math.max(1, Math.min(4, args.concurrency));

  args.onEvent({
    kind: "scan_complete",
    data: {
      folder:        "(browser-picked)",
      queued_count:  args.files.length,
      skipped_count: 0,
      queued: args.files.map((f) => {
        const probe = args.probedDurations.get(f.name);
        return {
          name:            f.name,
          path:            (f as unknown as { webkitRelativePath?: string }).webkitRelativePath || f.name,
          size_mb:         +(f.size / (1024 * 1024)).toFixed(2),
          duration_sec:    probe?.duration_sec ?? 0,
          eta_sec:         probe?.eta_sec ?? 30,
          transcript_name: probe?.transcript_name ?? `${stem(f.name)}_transcript.docx`,
          status:          "queued",
        };
      }),
      skipped: [],
    },
  });

  let cap = startCap;
  let succeeded = 0;
  let failed = 0;
  let activeWorkers = 0;
  const inflightDurations: number[] = [];
  const startedAt = Date.now();

  const filesIndexed = args.files.map((f, i) => ({ index: i, file: f }));

  async function processOne(index: number, file: File) {
    activeWorkers += 1;
    const probe = args.probedDurations.get(file.name);
    inflightDurations.push(probe?.duration_sec ?? 0);
    args.onEvent({
      kind: "file_start",
      data: { index, name: file.name, eta_sec: probe?.eta_sec ?? 30 },
    });
    try {
      args.onEvent({ kind: "file_progress", data: { index, name: file.name, percent: 5, stage: "uploading" } });
      const res = await notesClient.uploadTranscribeAudio(file, {
        title:                stem(file.name),
        language:             (args.language ?? "auto") as "auto" | "zh" | "ja" | "ko" | "en",
        translation_language: args.translation_language,
        note_type:            args.note_type,
      });
      args.onEvent({ kind: "file_progress", data: { index, name: file.name, percent: 70, stage: "writing_doc" } });

      // Pull the .docx and write/download it.
      const docxResp = await fetch(
        `${API_BASE}/notes/${res.data.note_id}/export.docx`,
      );
      if (!docxResp.ok) throw new Error(`export.docx ${docxResp.status}`);
      const blob = await docxResp.blob();

      const transcriptName = probe?.transcript_name ?? `${stem(file.name)}_transcript.docx`;
      if (args.dirHandle) {
        // Tier 2
        const transcriptsDir = await args.dirHandle.getDirectoryHandle("transcripts", { create: true });
        const fh = await transcriptsDir.getFileHandle(transcriptName, { create: true });
        const w = await fh.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        // Tier 3: trigger a browser download
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = transcriptName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      }

      succeeded += 1;
      args.onEvent({
        kind: "file_done",
        data: {
          index, name: file.name,
          transcript_path: transcriptName,
          elapsed_sec:     0,
          note_id:         res.data.note_id,
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 429 auto-throttle
      if (/429|rate/i.test(msg)) cap = Math.max(1, Math.floor(cap / 2));
      failed += 1;
      args.onEvent({ kind: "file_error", data: { index, name: file.name, error: msg } });
    } finally {
      const dur = probe?.duration_sec ?? 0;
      const idx = inflightDurations.indexOf(dur);
      if (idx !== -1) inflightDurations.splice(idx, 1);
      activeWorkers -= 1;
    }
  }

  // Dispatcher: launch workers up to `cap`, throttling on big files / 429s.
  const tasks: Promise<void>[] = [];
  for (const { index, file } of filesIndexed) {
    if (args.signal?.aborted) break;
    while (
      activeWorkers >= cap ||
      inflightDurations.some((d) => d > BIG_FILE_SECONDS)
    ) {
      if (args.signal?.aborted) break;
      await sleep(50);
    }
    if (args.signal?.aborted) break;
    tasks.push(processOne(index, file));
  }
  await Promise.all(tasks);

  args.onEvent({
    kind: "batch_done",
    data: {
      total:             args.files.length,
      succeeded, failed,
      skipped:           0,
      total_elapsed_sec: (Date.now() - startedAt) / 1000,
    },
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number) { return new Promise<void>((r) => setTimeout(r, ms)); }

function stem(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(0, dot) : filename;
}

// ---------------------------------------------------------------------------
// Probe (Tier 2/3)
// ---------------------------------------------------------------------------

export async function probeFile(file: File): Promise<{ duration_sec: number; eta_sec: number }> {
  const fd = new FormData();
  fd.append("audio", file);
  const resp = await fetch(`${API_BASE}/notes/probe-audio`, { method: "POST", body: fd });
  if (!resp.ok) {
    return { duration_sec: 0, eta_sec: 30 };
  }
  const json = await resp.json();
  return {
    duration_sec: json?.data?.duration_seconds ?? 0,
    eta_sec:      json?.data?.estimated_transcribe_seconds ?? 30,
  };
}
```

- [ ] **Step 2: Type-check**

```
cd frontend && npx tsc --noEmit
```
Expected: no errors in `batchTranscribeClient.ts`. Other pre-existing errors are out of scope.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/batchTranscribeClient.ts
git commit -m "feat(notes-fe): batch transcribe client -- tier detection + SSE + per-tier write-back"
```

---

## Task 9: Frontend — `BatchTranscribeModal` skeleton (PICK + SCAN)

**Files:**
- Create: `frontend/src/components/domain/notes/BatchTranscribeModal.tsx`

The modal opens, runs tier detection, shows a tier-specific PICK UI, runs the scan, then transitions to CONFIRM. Task 10 adds CONFIRM/RUNNING/DONE.

The TypeScript handle types for the File System Access API are not in `lib.dom` of every TS version in this repo, so we declare a narrow ambient type at the top. (Alternatively `npm i -D @types/wicg-file-system-access`, but a local declaration keeps this work self-contained.)

- [ ] **Step 1: Implement the modal skeleton**

Create `frontend/src/components/domain/notes/BatchTranscribeModal.tsx`:

```tsx
"use client";

/**
 * BatchTranscribeModal -- folder-batch transcription.
 *
 * State machine: PICK -> SCAN -> CONFIRM -> RUNNING -> DONE.
 *
 * Tier 1 (local):     paste folder path; backend writes transcripts directly.
 * Tier 2 (Chromium):  showDirectoryPicker; browser writes via FS Access API.
 * Tier 3 (else):      <input webkitdirectory>; transcripts download to Downloads/.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Folder, Loader2, X } from "lucide-react";

import {
  detectTier, tierLabel, probeFile,
  type Tier, type ScanFile, type ScanSkip,
} from "@/lib/api/batchTranscribeClient";

// FS Access API typings -- conservative ambient declaration so this
// component compiles regardless of `lib.dom`'s vintage in tsconfig.
declare global {
  interface Window {
    showDirectoryPicker?: (opts?: { mode?: "read" | "readwrite" }) => Promise<FileSystemDirectoryHandle>;
  }
  interface FileSystemDirectoryHandle {
    name:                string;
    values:              () => AsyncIterable<FileSystemHandle>;
    getDirectoryHandle:  (name: string, opts?: { create?: boolean }) => Promise<FileSystemDirectoryHandle>;
    getFileHandle:       (name: string, opts?: { create?: boolean }) => Promise<FileSystemFileHandle>;
  }
  interface FileSystemFileHandle {
    name:           string;
    kind:           "file";
    getFile:        () => Promise<File>;
    createWritable: () => Promise<FileSystemWritableFileStream>;
  }
  interface FileSystemHandle { kind: "file" | "directory"; name: string; }
  interface FileSystemWritableFileStream {
    write: (data: Blob) => Promise<void>;
    close: () => Promise<void>;
  }
}

const ALLOWED = [
  ".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm",
  ".mp4", ".mov", ".mkv", ".avi", ".m4v",
];

type State = "PICK" | "SCAN" | "CONFIRM" | "RUNNING" | "DONE";

interface Props {
  onClose: () => void;
  onComplete: () => void;
}

export default function BatchTranscribeModal({ onClose, onComplete }: Props) {
  const [tier, setTier] = useState<Tier>(1);
  const [tierOverride, setTierOverride] = useState<Tier | null>(null);
  const effectiveTier = tierOverride ?? tier;

  const [state, setState] = useState<State>("PICK");
  const [error, setError] = useState<string | null>(null);

  // Pick state
  const [folderPath, setFolderPath]     = useState<string>("");
  const [pickedFiles, setPickedFiles]   = useState<File[]>([]);
  const [dirHandle, setDirHandle]       = useState<FileSystemDirectoryHandle | null>(null);

  // Scan results (used by Task 10)
  const [scanQueued,  setScanQueued]  = useState<ScanFile[]>([]);
  const [scanSkipped, setScanSkipped] = useState<ScanSkip[]>([]);
  const [scanFolder,  setScanFolder]  = useState<string>("");

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setTier(detectTier()); }, []);

  // ---------- PICK handlers ----------

  async function pickViaFsa() {
    setError(null);
    try {
      if (!window.showDirectoryPicker) throw new Error("Browser does not support showDirectoryPicker.");
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      const files: File[] = [];
      for await (const child of handle.values()) {
        if (child.kind !== "file") continue;
        const f = await (child as FileSystemFileHandle).getFile();
        if (ALLOWED.some((ext) => f.name.toLowerCase().endsWith(ext))) {
          files.push(f);
        }
      }
      setDirHandle(handle);
      setPickedFiles(files);
      await runScanTier23(files);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function pickViaInput(ev: React.ChangeEvent<HTMLInputElement>) {
    const fl = ev.target.files;
    if (!fl) return;
    const files: File[] = [];
    for (let i = 0; i < fl.length; i += 1) {
      const f = fl[i];
      if (ALLOWED.some((ext) => f.name.toLowerCase().endsWith(ext))) files.push(f);
    }
    setPickedFiles(files);
    void runScanTier23(files);
  }

  async function runScanTier1() {
    if (!folderPath.trim()) { setError("Please paste a folder path."); return; }
    setState("SCAN");
    setError(null);
    try {
      // Tier 1's scan runs server-side as part of /batch-transcribe-folder, but
      // we want to show the file list BEFORE running. Cheap approach: hit the
      // batch endpoint with a separate dry-run flag... we don't have one.
      // For v1 we just go straight to CONFIRM with a placeholder count and let
      // the user click Start, then render scan_complete in RUNNING. To avoid
      // that, do an HTTP call to a lightweight "dry-run" alias by calling the
      // same endpoint but only consuming the first event. This keeps the UX
      // honest without a separate endpoint.
      const { runBatchTier1 } = await import("@/lib/api/batchTranscribeClient");
      const ac = new AbortController();
      let scanReceived = false;
      await runBatchTier1({
        folder_path:          folderPath.trim(),
        translation_language: "en",   // overwritten before RUNNING starts
        note_type:            "meeting_transcript",
        language:             null,
        concurrency:          1,
        signal:               ac.signal,
        onEvent: (ev) => {
          if (ev.kind === "scan_complete") {
            scanReceived = true;
            setScanQueued(ev.data.queued);
            setScanSkipped(ev.data.skipped);
            setScanFolder(ev.data.folder);
            ac.abort();    // we only wanted the scan; cancel the rest
          }
        },
      }).catch(() => { /* expected: aborted */ });
      if (!scanReceived) throw new Error("Scan did not return any files. Check the folder path.");
      setState("CONFIRM");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("PICK");
    }
  }

  async function runScanTier23(files: File[]) {
    setState("SCAN");
    setError(null);
    try {
      // Probe each file in parallel (small) -- builds the same shape as the
      // server-side scan_complete event.
      const probes = await Promise.all(files.map(async (f) => {
        const p = await probeFile(f);
        const dot = f.name.lastIndexOf(".");
        const stem = dot > 0 ? f.name.slice(0, dot) : f.name;
        return {
          file: f,
          probe: { ...p, transcript_name: `${stem}_transcript.docx` },
        };
      }));
      const queued: ScanFile[] = probes.map(({ file, probe }) => ({
        name:            file.name,
        path:            (file as unknown as { webkitRelativePath?: string }).webkitRelativePath || file.name,
        size_mb:         +(file.size / (1024 * 1024)).toFixed(2),
        duration_sec:    probe.duration_sec,
        eta_sec:         probe.eta_sec,
        transcript_name: probe.transcript_name,
        status:          "queued",
      }));
      setScanQueued(queued);
      setScanSkipped([]);
      setScanFolder(files[0] ? "(browser-picked)" : "");
      setState("CONFIRM");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("PICK");
    }
  }

  // ---------- Render ----------

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === e.currentTarget && state !== "RUNNING") onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <Folder size={16} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-800">Batch transcribe folder</h2>
            <span className={`ml-3 text-[10px] px-2 py-0.5 rounded-full
              ${effectiveTier === 1 ? "bg-emerald-50 text-emerald-700" :
                effectiveTier === 2 ? "bg-amber-50 text-amber-700" :
                                      "bg-orange-50 text-orange-700"}
            `}>
              {tierLabel(effectiveTier)}
            </span>
          </div>
          <button onClick={onClose} disabled={state === "RUNNING"}
                  className="text-slate-400 hover:text-slate-600 disabled:opacity-40">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4 min-h-[18rem]">
          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              {error}
            </div>
          )}

          {state === "PICK" && (
            <PickStateUi
              tier={effectiveTier}
              folderPath={folderPath} setFolderPath={setFolderPath}
              fileInputRef={fileInputRef}
              onPickFsa={pickViaFsa}
              onPickInput={pickViaInput}
              onConfirmTier1={runScanTier1}
            />
          )}

          {state === "SCAN" && (
            <div className="flex items-center gap-2 text-sm text-indigo-600">
              <Loader2 size={14} className="animate-spin" />
              Scanning folder...
            </div>
          )}

          {state === "CONFIRM" && (
            <div className="text-xs text-slate-500">
              {/* Filled in by Task 10. Placeholder so the skeleton compiles. */}
              Scanned {scanQueued.length} file(s); skipped {scanSkipped.length}. Folder: {scanFolder || "(none)"}.
            </div>
          )}

          {state === "RUNNING" && <div className="text-xs">Running... (Task 10)</div>}
          {state === "DONE"    && <div className="text-xs">Done. (Task 10)</div>}
        </div>

        {/* Tier override (advanced) */}
        <div className="px-5 pb-3">
          <details className="text-[10px] text-slate-400">
            <summary className="cursor-pointer">Advanced</summary>
            <div className="mt-2 flex items-center gap-2">
              <span>Override tier:</span>
              {[1, 2, 3].map((t) => (
                <button key={t}
                        onClick={() => setTierOverride(t as Tier)}
                        disabled={state === "RUNNING"}
                        className={`px-2 py-0.5 rounded-md border ${effectiveTier === t ? "bg-indigo-50 border-indigo-300 text-indigo-700" : "border-slate-200"}`}>
                  Tier {t}
                </button>
              ))}
              {tierOverride !== null && (
                <button onClick={() => setTierOverride(null)} className="text-slate-500 underline">reset</button>
              )}
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

interface PickStateProps {
  tier:            Tier;
  folderPath:      string;
  setFolderPath:   (s: string) => void;
  fileInputRef:    React.RefObject<HTMLInputElement>;
  onPickFsa:       () => void;
  onPickInput:     (ev: React.ChangeEvent<HTMLInputElement>) => void;
  onConfirmTier1:  () => void;
}

function PickStateUi({
  tier, folderPath, setFolderPath, fileInputRef, onPickFsa, onPickInput, onConfirmTier1,
}: PickStateProps) {
  if (tier === 1) {
    return (
      <div className="space-y-3">
        <label className="block text-[11px] font-semibold text-slate-600">Folder path on this machine</label>
        <input
          type="text"
          value={folderPath}
          onChange={(e) => setFolderPath(e.target.value)}
          placeholder="D:\\recordings\\Q1-earnings"
          className="w-full h-9 px-3 rounded-md border border-slate-200 bg-white text-sm font-mono outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        <button onClick={onConfirmTier1}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Scan folder
        </button>
        <p className="text-[10px] text-slate-400">Subfolders are not scanned.</p>
      </div>
    );
  }
  if (tier === 2) {
    return (
      <div className="space-y-3">
        <button onClick={onPickFsa}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Pick folder
        </button>
        <p className="text-[10px] text-slate-400">
          A native folder picker will open. Grant read+write so transcripts can be saved back.
          Subfolders are not scanned.
        </p>
      </div>
    );
  }
  // Tier 3
  return (
    <div className="space-y-3">
      <input
        ref={fileInputRef}
        type="file"
        // @ts-expect-error -- non-standard but supported in Chrome/Edge/Firefox/Safari
        webkitdirectory=""
        directory=""
        multiple
        className="hidden"
        onChange={onPickInput}
      />
      <button onClick={() => fileInputRef.current?.click()}
              className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
        Pick folder
      </button>
      <p className="text-[10px] text-slate-400">
        Transcripts will download to your browser's Downloads folder. Subfolders are not scanned.
      </p>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```
cd frontend && npx tsc --noEmit
```
Expected: no errors in this file.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/domain/notes/BatchTranscribeModal.tsx
git commit -m "feat(notes-fe): BatchTranscribeModal skeleton with tier-aware folder picker"
```

---

## Task 10: Frontend — CONFIRM + RUNNING + DONE states

**Files:**
- Modify: `frontend/src/components/domain/notes/BatchTranscribeModal.tsx`

Add the file-list rendering, the Start button, the SSE-driven progress UI, and the DONE summary.

- [ ] **Step 1: Add inputs + start trigger to component state**

In `BatchTranscribeModal.tsx`, add to the existing state hooks (just below the scan state hooks):

```tsx
  // Translation / type pickers (whole-batch config)
  const [translation, setTranslation] = useState<string>("en");
  const [noteType,    setNoteType]    = useState<string>("meeting_transcript");
  const [language,    setLanguage]    = useState<string | null>(null);

  // RUNNING state -- per-file row state keyed by index
  type RowState = {
    name:        string;
    status:      "queued" | "in_flight" | "done" | "error";
    percent?:    number;
    stage?:      string;
    elapsed_sec?: number;
    error?:       string;
    eta_sec?:     number;
  };
  const [rows, setRows] = useState<Record<number, RowState>>({});
  const [overallTotalEta, setOverallTotalEta] = useState<number>(0);
  const abortRef = useRef<AbortController | null>(null);

  // DONE summary
  const [summary, setSummary] = useState<{ succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } | null>(null);
```

- [ ] **Step 2: Add the Start handler + per-tier dispatch**

Inside `BatchTranscribeModal`, add this method:

```tsx
  async function handleStart() {
    setState("RUNNING");
    setError(null);
    setSummary(null);

    // Initialize row state from the scan
    const initial: Record<number, RowState> = {};
    scanQueued.forEach((q, i) => {
      initial[i] = { name: q.name, status: "queued", eta_sec: q.eta_sec };
    });
    setRows(initial);
    setOverallTotalEta(scanQueued.reduce((s, q) => s + q.eta_sec, 0));

    abortRef.current = new AbortController();
    const onEvent = (ev: import("@/lib/api/batchTranscribeClient").BatchEvent) => {
      switch (ev.kind) {
        case "scan_complete":
          // Tier 2/3 sends this too -- merge in (in case probe missed something)
          setScanQueued(ev.data.queued);
          setScanSkipped(ev.data.skipped);
          break;
        case "file_start":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || {}), name: ev.data.name, status: "in_flight", eta_sec: ev.data.eta_sec } }));
          break;
        case "file_progress":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), percent: ev.data.percent, stage: ev.data.stage } }));
          break;
        case "file_done":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), status: "done", percent: 100, elapsed_sec: ev.data.elapsed_sec } }));
          break;
        case "file_error":
          setRows((r) => ({ ...r, [ev.data.index]: { ...(r[ev.data.index] || { name: ev.data.name, status: "in_flight" }), status: "error", error: ev.data.error } }));
          break;
        case "batch_done":
          setSummary(ev.data);
          setState("DONE");
          break;
        case "batch_error":
          setError(ev.data.error);
          setState("DONE");
          break;
      }
    };

    try {
      if (effectiveTier === 1) {
        const { runBatchTier1 } = await import("@/lib/api/batchTranscribeClient");
        await runBatchTier1({
          folder_path:          folderPath.trim(),
          translation_language: translation,
          note_type:            noteType,
          language,
          concurrency:          2,
          signal:               abortRef.current.signal,
          onEvent,
        });
      } else {
        const { runBatchClient } = await import("@/lib/api/batchTranscribeClient");
        // Build probedDurations map from scan results
        const probedMap = new Map<string, { duration_sec: number; eta_sec: number; transcript_name: string }>();
        scanQueued.forEach((q) => probedMap.set(q.name, {
          duration_sec: q.duration_sec, eta_sec: q.eta_sec, transcript_name: q.transcript_name,
        }));
        await runBatchClient({
          files:                pickedFiles,
          dirHandle:            dirHandle ?? undefined,
          translation_language: translation,
          note_type:            noteType,
          language,
          concurrency:          2,
          signal:               abortRef.current.signal,
          probedDurations:      probedMap,
          onEvent,
        });
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setError(e instanceof Error ? e.message : String(e));
      setState("DONE");
    }
  }

  function handleCancel() {
    abortRef.current?.abort();
  }
```

- [ ] **Step 3: Replace the placeholder bodies for CONFIRM / RUNNING / DONE**

Replace the three lines in the body section:

```tsx
{state === "CONFIRM" && <ConfirmStateUi
  scanQueued={scanQueued} scanSkipped={scanSkipped} folder={scanFolder}
  translation={translation} setTranslation={setTranslation}
  noteType={noteType} setNoteType={setNoteType}
  onStart={handleStart}
/>}
{state === "RUNNING" && <RunningStateUi
  scanQueued={scanQueued} rows={rows} totalEta={overallTotalEta}
  onCancel={handleCancel}
/>}
{state === "DONE" && <DoneStateUi
  summary={summary} folder={scanFolder} onClose={() => { onComplete(); onClose(); }}
/>}
```

And add the three sub-components at the bottom of the file:

```tsx
function ConfirmStateUi(props: {
  scanQueued: ScanFile[]; scanSkipped: ScanSkip[]; folder: string;
  translation: string; setTranslation: (s: string) => void;
  noteType: string; setNoteType: (s: string) => void;
  onStart: () => void;
}) {
  const totalEta = props.scanQueued.reduce((s, q) => s + q.eta_sec, 0);
  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-600">
        Folder: <span className="font-mono">{props.folder}</span><br/>
        {props.scanQueued.length} to process; {props.scanSkipped.length} already done (skipped).
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-[10px] font-semibold text-slate-500 uppercase">Translation</span>
          <select value={props.translation} onChange={(e) => props.setTranslation(e.target.value)}
                  className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm">
            <option value="none">(no translation)</option>
            <option value="en">English</option>
            <option value="zh-hans">Simplified Chinese</option>
            <option value="zh-hant">Traditional Chinese</option>
            <option value="ja">Japanese</option>
            <option value="ko">Korean</option>
          </select>
        </label>
        <label className="block">
          <span className="text-[10px] font-semibold text-slate-500 uppercase">Note type</span>
          <select value={props.noteType} onChange={(e) => props.setNoteType(e.target.value)}
                  className="w-full h-9 px-2 rounded-md border border-slate-200 bg-white text-sm">
            <option value="meeting_transcript">Meeting Transcript</option>
            <option value="earnings_call">Earnings Call</option>
            <option value="management_meeting">Mgmt Meeting</option>
            <option value="conference">Conference</option>
            <option value="internal">Internal</option>
          </select>
        </label>
      </div>
      <div className="border border-slate-200 rounded-md max-h-72 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-[10px] uppercase text-slate-500">
            <tr><th className="text-left px-3 py-1.5">#</th><th className="text-left">File</th><th className="text-right">Size</th><th className="text-right">Duration</th><th className="text-right pr-3">ETA</th></tr>
          </thead>
          <tbody>
            {props.scanQueued.map((q, i) => (
              <tr key={`q-${i}`} className="border-t border-slate-100">
                <td className="px-3 py-1.5">{i + 1}</td>
                <td className="font-mono">{q.name}{q.size_mb > 1024 && <span className="ml-2 text-amber-600">large file -- upload may take a while</span>}</td>
                <td className="text-right">{q.size_mb.toFixed(1)} MB</td>
                <td className="text-right">{fmtSec(q.duration_sec)}</td>
                <td className="text-right pr-3">~{fmtSec(q.eta_sec)}</td>
              </tr>
            ))}
            {props.scanSkipped.map((s, i) => (
              <tr key={`s-${i}`} className="border-t border-slate-100 text-slate-400">
                <td className="px-3 py-1.5">&#10003;</td>
                <td className="font-mono italic">{s.name} (already transcribed -- skipped)</td>
                <td/><td/><td/>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex justify-between items-center">
        <span className="text-xs text-slate-500">Total ETA: ~{fmtSec(totalEta)}</span>
        <button onClick={props.onStart}
                disabled={props.scanQueued.length === 0}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700 disabled:bg-slate-300">
          Start transcription
        </button>
      </div>
    </div>
  );
}

function RunningStateUi(props: {
  scanQueued: ScanFile[];
  rows: Record<number, { name: string; status: string; percent?: number; stage?: string; elapsed_sec?: number; error?: string; eta_sec?: number }>;
  totalEta: number;
  onCancel: () => void;
}) {
  const done    = Object.values(props.rows).filter((r) => r.status === "done").length;
  const failed  = Object.values(props.rows).filter((r) => r.status === "error").length;
  const total   = props.scanQueued.length;
  const overall = total === 0 ? 0 : Math.round(((done + failed) / total) * 100);
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
          <div className="h-3 bg-indigo-500" style={{ width: `${overall}%` }} />
        </div>
        <span className="text-xs text-slate-600 w-32 text-right">{done + failed}/{total} files</span>
      </div>
      <div className="border border-slate-200 rounded-md max-h-80 overflow-y-auto">
        <table className="w-full text-xs">
          <tbody>
            {props.scanQueued.map((q, i) => {
              const r = props.rows[i] || { name: q.name, status: "queued", eta_sec: q.eta_sec };
              const icon =
                r.status === "done"      ? <span className="text-emerald-600">&#10003;</span> :
                r.status === "in_flight" ? <span className="text-indigo-500">&#8635;</span> :
                r.status === "error"     ? <span className="text-red-500">&#10005;</span> :
                                           <span className="text-slate-400">&#8987;</span>;
              return (
                <tr key={i} className="border-t border-slate-100">
                  <td className="px-3 py-1.5 w-6">{icon}</td>
                  <td className="font-mono">{q.name}</td>
                  <td className="px-3 py-1.5">
                    {r.status === "in_flight" && (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div className="h-1.5 bg-indigo-400" style={{ width: `${r.percent ?? 5}%` }} />
                        </div>
                        <span className="text-[10px] text-slate-500 whitespace-nowrap">
                          {r.percent ?? 5}% &middot; {r.stage}
                        </span>
                      </div>
                    )}
                    {r.status === "done"  && <span className="text-emerald-700 text-[11px]">Done {r.elapsed_sec ? `in ${fmtSec(r.elapsed_sec)}` : ""}</span>}
                    {r.status === "error" && <span className="text-red-600 text-[11px]">{r.error}</span>}
                    {r.status === "queued" && <span className="text-slate-400 text-[11px]">Queued</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex justify-end">
        <button onClick={props.onCancel} className="h-8 px-3 text-xs font-medium text-slate-600 hover:text-red-600">
          Cancel batch
        </button>
      </div>
    </div>
  );
}

function DoneStateUi(props: {
  summary: { succeeded: number; failed: number; skipped: number; total_elapsed_sec: number } | null;
  folder: string;
  onClose: () => void;
}) {
  return (
    <div className="space-y-3 text-sm">
      <div className="text-emerald-700 font-semibold">Batch complete.</div>
      {props.summary && (
        <ul className="text-xs space-y-1">
          <li>Succeeded: <b>{props.summary.succeeded}</b></li>
          <li>Failed:    <b>{props.summary.failed}</b></li>
          <li>Skipped:   <b>{props.summary.skipped}</b></li>
          <li>Elapsed:   <b>{fmtSec(props.summary.total_elapsed_sec)}</b></li>
        </ul>
      )}
      <p className="text-[11px] text-slate-500">Transcripts written to <span className="font-mono">{props.folder}/transcripts/</span></p>
      <div className="flex justify-end">
        <button onClick={props.onClose}
                className="h-9 px-4 bg-indigo-600 text-white text-xs font-semibold rounded-md hover:bg-indigo-700">
          Close
        </button>
      </div>
    </div>
  );
}

function fmtSec(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return "0s";
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}
```

- [ ] **Step 4: Type-check**

```
cd frontend && npx tsc --noEmit
```
Expected: no new errors in `BatchTranscribeModal.tsx`. Pre-existing TS errors elsewhere are out of scope.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/domain/notes/BatchTranscribeModal.tsx
git commit -m "feat(notes-fe): batch-modal CONFIRM/RUNNING/DONE states with progress UI"
```

---

## Task 11: Frontend — wire button into NotesView + concurrent-batch guard

**Files:**
- Modify: `frontend/src/app/(dashboard)/notes/NotesView.tsx`

Add a "Batch folder" button next to the existing "Upload Audio" button. When clicked, open `BatchTranscribeModal`. While a batch is RUNNING, disable both upload buttons (per-tab guard).

- [ ] **Step 1: Add the import + state plumbing**

In `NotesView.tsx`, add the import near the existing one:

```tsx
import BatchTranscribeModal from "@/components/domain/notes/BatchTranscribeModal";
```

In the `Props` interface, alongside `showUploadModal`, add:

```tsx
  showBatchModal:    boolean;
  onOpenBatch:       () => void;
  onCloseBatch:      () => void;
  onBatchComplete:   () => void;
```

In the destructure of `NotesView` props, add `showBatchModal, onOpenBatch, onCloseBatch, onBatchComplete`.

- [ ] **Step 2: Add the button**

Find the existing "Upload Audio" button block (around line 749). Immediately after the closing `</button>` of the Upload Audio button, add:

```tsx
          <button
            onClick={onOpenBatch}
            disabled={showBatchModal /* per-tab guard */}
            className="flex items-center gap-2 h-9 px-3 border border-slate-200 text-slate-700 text-sm font-medium rounded-md hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="Process every audio/video file in a folder; one transcript .docx per file"
          >
            <Folder size={14} />
            Batch folder
          </button>
```

Add `Folder` to the icon imports from lucide-react at the top of the file.

- [ ] **Step 3: Mount the modal**

Below the existing `<AudioUploadModal>` mount block (around line 885), add:

```tsx
      {showBatchModal && (
        <BatchTranscribeModal onClose={onCloseBatch} onComplete={onBatchComplete} />
      )}
```

- [ ] **Step 4: Wire state into the container**

Find `NotesContainer.tsx` (in the same directory) and add the `showBatchModal` state pattern alongside the existing `showUploadModal`:

```tsx
  const [showBatchModal, setShowBatchModal] = useState(false);
  const onOpenBatch     = () => setShowBatchModal(true);
  const onCloseBatch    = () => setShowBatchModal(false);
  const onBatchComplete = () => { setShowBatchModal(false); /* nothing else to refresh -- notes show in their group */ };
```

And pass them through to `NotesView`. (Look for where `showUploadModal`, `onOpenUpload`, etc. are wired and follow that pattern exactly.)

- [ ] **Step 5: Manual smoke-test**

Start backend (`uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`) and frontend (`cd frontend && npm run dev`). Open the Notes page in Chrome. Click "Batch folder". The modal should open and show "Local mode -- transcripts saved directly to folder".

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/(dashboard)/notes/NotesView.tsx frontend/src/app/(dashboard)/notes/NotesContainer.tsx
git commit -m "feat(notes-fe): mount BatchTranscribeModal from NotesView with per-tab guard"
```

---

## Task 12: End-to-end verification

This task is verification only — no new code. Walk through the spec's 10 acceptance criteria with real fixtures.

**Setup:** create a fixture folder somewhere accessible to the dev backend, e.g. `D:\test_batch_fixtures\`, containing five short (~30 sec each) clips in mixed formats:
- `clip1.mp3`, `clip2.wav`, `clip3.mp4`, `clip4.m4a`, `clip5.opus`

You can record short clips via the existing live-record feature, or use ffmpeg to generate test tones:
```
ffmpeg -f lavfi -i "sine=frequency=440:duration=30" -c:a libmp3lame clip1.mp3
ffmpeg -f lavfi -i "sine=frequency=440:duration=30" clip2.wav
ffmpeg -f lavfi -i "sine=frequency=440:duration=30" -c:a aac clip3.mp4
ffmpeg -f lavfi -i "sine=frequency=440:duration=30" clip4.m4a
ffmpeg -f lavfi -i "sine=frequency=440:duration=30" clip5.opus
```
(Sine-wave audio transcribes to nothing, but exercises the whole pipeline.)

- [ ] **AC1 — Format support**
  Drop a `.mp4` into the existing single-file upload modal. Confirm upload succeeds and a transcript note is created. Repeat for `.mov`, `.mkv`. (Use a real short MP4 clip with audio to make the run meaningful — sine-wave video has no audio track and will produce an empty transcript, which is also acceptable for the format-acceptance test.)

- [ ] **AC2 — Tier 1 happy path**
  In the Notes page (Chrome on localhost), click "Batch folder". Modal shows "Local mode". Paste `D:\test_batch_fixtures`. Click Scan. Confirm 5 files listed. Click Start. Confirm:
  - 5 `_transcript.docx` files appear in `D:\test_batch_fixtures\transcripts\`
  - Modal DONE shows succeeded=5, failed=0, skipped=0
  - Each docx opens cleanly in Word

- [ ] **AC3 — Skip-already-done**
  Re-run the same batch on the same folder. Modal scan reports all 5 as skipped. Click Start (with no queued files): the batch finishes immediately with skipped=5, succeeded=0.

- [ ] **AC4 — Tier 2 happy path** (Chrome/Edge)
  In the modal, expand Advanced and override to Tier 2. Click "Pick folder", select `D:\test_batch_fixtures`. Confirm 5 files listed (all skipped from previous run; remove the transcripts/ folder first to make it interesting). Run. Verify transcripts land in `D:\test_batch_fixtures\transcripts\` (written by the browser via FSA API).

- [ ] **AC5 — Tier 3 fallback** (Firefox)
  Open the same Notes page in Firefox. Modal shows "Browser mode -- transcripts download to your Downloads folder". Pick the folder. Run. Confirm five `_transcript.docx` files arrive in your browser's Downloads/.

- [ ] **AC6 — Big-file safety**
  Add a 95-minute `.mp3` to the fixtures (or temporarily bump `BIG_FILE_SECONDS = 30` in `batch_runner.py` for a quick local test). Run batch with concurrency=2 and confirm the runner observably throttles — only one file in flight while the long file is running. Restore the constant after the test.

- [ ] **AC7 — Failure isolation**
  Add a zero-byte file `corrupt.mp3` to the fixtures. Run the batch. Confirm DONE shows failed=1 with the others succeeded; the modal shows `corrupt.mp3` in red with the error message; transcripts/ contains the others.

- [ ] **AC8 — Filename collision**
  Add `meeting.mp3` and `meeting.mp4` to the fixtures (use the same source clip in two formats). Run. Confirm transcripts/ ends up with both `meeting_mp3_transcript.docx` and `meeting_mp4_transcript.docx`.

- [ ] **AC9 — Cancellation**
  Start a batch on a fresh fixtures folder. Mid-run, click "Cancel batch". Confirm the in-flight file finishes its current step but no new files start; modal moves to DONE with succeeded < total.

- [ ] **AC10 — Auth gate**
  *Note:* the existing `/upload-transcribe` endpoint uses a hardcoded `TENANT_ID = "Institutional_L1"` placeholder; there's no real auth dependency to gate against today. Confirm the new endpoints behave the same as `/upload-transcribe` w.r.t. auth -- i.e. they don't bypass anything that exists. If a real auth dependency is added later, the new endpoints inherit it because they use the same `Depends(get_db_session)` chain.

- [ ] **Final:** mark all 10 acceptance criteria done, then commit any small follow-up fixes uncovered during verification:

```bash
git add -A
git commit -m "fix(notes): follow-ups from end-to-end verification"
```

---

## Self-review (run after writing the plan)

This checklist was applied while writing this plan. Each spec section maps to a task:

| Spec section | Task |
|---|---|
| Tier detection (3-tier matrix) | 8 (`detectTier`), 9 (PICK UI per tier) |
| Extension whitelist | 1 |
| `POST /probe-audio` | 3 |
| `POST /batch-transcribe-folder` (SSE protocol, scan logic, per-file pipeline) | 4 (scan), 5 (sequential runner), 6 (concurrency), 7 (endpoint + SSE wiring) |
| Concurrency: bounded-2 + auto-throttle | 6 |
| Folder scan: skip detection, collision, sort, subfolder-ignore, ffprobe | 4 |
| Filename collision disambiguation | 4 |
| Per-file cleanup of intermediate audio | inherited from existing `/upload-transcribe`; runner doesn't add new intermediate files |
| `_results/` raw Gemini JSON safety net | inherited from existing `/upload-transcribe` (Tier 2/3); for Tier 1 the existing `save_polished_transcript` writes the meta JSON to DB |
| Refactor docx builder | 2 |
| Frontend modal state machine | 9, 10 |
| File-list with skip rows | 10 (ConfirmStateUi) |
| Big-file warning row in CONFIRM | 10 (handled in ConfirmStateUi via `q.size_mb > 1024` check) |
| Per-tier output writing (Tier 2 FSA, Tier 3 download) | 8 (runBatchClient) |
| Progress fidelity (Tier 1 granular, Tier 2/3 coarse) | 8 (orchestrator emits coarse stages); 10 (UI rendering) |
| Cancellation | 8 (AbortController in runBatchTier1, signal threaded through runBatchClient); 10 (handleCancel) |
| Concurrent-batch guard | 11 (button-disabled while showBatchModal is true) |
| Acceptance criteria 1-10 | 12 |

No placeholders, no "TBD", every step has concrete code or a concrete command.

Type-consistency check:
- `ScanFile`, `ScanSkip`, `ScanResult` defined in Task 4 (backend) and re-typed in Task 8 (frontend, same shape)
- `BatchEvent` discriminated union matches the runner's emitted events
- `BatchOptions` matches `BatchTranscribeRequest` body
- `_ALLOWED_AUDIO_EXT` (backend) and `ALLOWED` array (frontend in Task 1 + Task 9) hold the same set
