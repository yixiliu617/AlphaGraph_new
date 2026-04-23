# URL Ingest for Meeting Notes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user paste a YouTube / podcast / video URL into the note editor and get the same 4-section editor content (user notes / AI summary / raw transcript / polished transcript) that a live recording produces — using existing manual captions when available (fast, ~free) and falling back to audio download + Gemini transcription when not.

**Architecture:** A new `url_ingest_service` orchestrates the flow: try yt-dlp manual captions → if found, parse VTT into segments and feed to a new `gemini_polish_text` call for summary generation → if not, download audio via yt-dlp and run the existing `gemini_batch_transcribe`. Either path returns the same dict shape as `gemini_batch_transcribe` so the downstream persistence + WebSocket message + frontend editor-insert flow stays unchanged. A new WebSocket endpoint streams progress; a new top-bar button + modal fires it.

**Tech Stack:** `yt-dlp` (new dependency) for caption fetching and audio extraction, existing Gemini 2.5 Flash integration, FastAPI WebSocket, React + TipTap (no new frontend libs). Tests via pytest. Frontend verification via `npx tsc --noEmit` + manual smoke.

**User preferences recorded in this plan:**
- Captions: manual only, never auto-generated. Fallback = audio.
- Trigger: top-bar button next to "Record Audio".
- Timeout: 60 min on the audio path.
- Progress UI: WebSocket streaming status, mirrors `_run_live_v2_session`.
- Store `source_url` on the note.
- Commits: **one clean commit at the end of the plan**, per "hybrid, clean-from-here-forward" working agreement. Intermediate tasks do not commit.

**Out of scope:**
- Auto-generated captions (decision Q1=b).
- Chunking of >60-min audio; we fail cleanly with a clear error.
- Mixing multiple sources (mic + URL, or two URLs).
- Re-ingesting a note from the same URL (if you need it, delete and re-create).
- Chat-agent `ingest_url` tool (comes free in Plan 3 once the chat agent lands; this plan just makes the backend callable).

---

## File Structure

**Backend — create:**
- `backend/app/services/url_ingest_service.py` — the orchestration layer. Three public functions:
  - `try_fetch_manual_captions(url, lang_hint) -> dict | None` — returns `{language, segments, source_caption_lang}` or `None`.
  - `download_audio(url, out_path) -> str` — returns the path to the downloaded OPUS file.
  - `ingest_url(url, note_id, language_hint, progress_cb) -> dict` — end-to-end; returns the same shape as `gemini_batch_transcribe`.
- `backend/tests/unit/test_url_ingest_service.py` — VTT parser tests, caption-path/audio-path branching tests (yt-dlp mocked).

**Backend — modify:**
- `backend/requirements.txt` — add `yt-dlp>=2024.12.13`.
- `backend/app/services/live_transcription.py` — new `gemini_polish_text(transcript_text, language_hint, note_id) -> dict` function reusing the existing `_parse_polish_response`. Same return shape as `gemini_batch_transcribe`; different prompt.
- `backend/app/models/orm/note_orm.py` — add `source_url = Column(String, nullable=True)`.
- `backend/app/models/domain/meeting_note.py` — add `source_url: Optional[str] = None`.
- `backend/app/services/notes_service.py` — `_to_orm` / `_to_domain` carry `source_url`.
- `backend/app/api/routers/v1/notes.py` — new `@router.websocket("/ws/ingest-url/{note_id}")`; reuses the existing persistence + WS message patterns from `_run_live_v2_session`.
- `backend/tests/unit/test_live_transcription_parse.py` — add test for `gemini_polish_text` parser behaviour (text-input path).
- `alphagraph.db` — ALTER TABLE ADD COLUMN `source_url`.

**Frontend — create:**
- `frontend/src/components/domain/notes/UrlIngestModal.tsx` — modal with URL input + language select + progress view; opens a WebSocket, mirrors `RecordingPanel`'s status handling.

**Frontend — modify:**
- `frontend/src/lib/api/notesClient.ts` — `NoteStub.source_url`, `ingestUrlWsUrl()` helper.
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx` — top-bar button `[Ingest URL]`, "Ingested from …" chip under the title, thread new props.
- `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx` — `showUrlIngestModal` state, `handleOpenUrlIngest` / `handleCloseUrlIngest`; reuse the existing `handleRecordingComplete` for the success path (zero new editor-insert logic).
- `frontend/src/app/(dashboard)/notes/[id]/store.ts` — `showUrlIngestModal` boolean + setter.

---

## Task 1: Dependency + data model

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/models/orm/note_orm.py`
- Modify: `backend/app/models/domain/meeting_note.py`
- Modify: `backend/app/services/notes_service.py`
- Run: one-shot SQLite ALTER on `alphagraph.db`

- [ ] **Step 1: Add yt-dlp to requirements**

Edit `backend/requirements.txt`. Add after the `edgartools>=2.0.0` line (alphabetical-ish with the other `*-dlp` / media deps):

```
yt-dlp>=2024.12.13
```

- [ ] **Step 2: Install it**

Run (from repo root):

```bash
pip install "yt-dlp>=2024.12.13"
```

Expected output: `Successfully installed yt-dlp-<version>`.

- [ ] **Step 3: Verify the install**

Run:

```bash
python -c "import yt_dlp; print(yt_dlp.version.__version__)"
```

Expected: a version string like `2024.12.13` or newer.

- [ ] **Step 4: Add `source_url` ORM column**

Edit `backend/app/models/orm/note_orm.py`. Find this block:

```python
    # Recording
    recording_path = Column(String, nullable=True)
    recording_mode = Column(String, nullable=True)   # "wasapi" | "browser"
    duration_seconds = Column(Integer, nullable=True)
```

Replace with:

```python
    # Recording
    recording_path = Column(String, nullable=True)
    recording_mode = Column(String, nullable=True)   # "wasapi" | "browser"
    duration_seconds = Column(Integer, nullable=True)
    # Set when the note was populated from a URL (YouTube / podcast / video).
    # null for mic/system-audio recordings.
    source_url = Column(String, nullable=True)
```

- [ ] **Step 5: Add `source_url` domain field**

Edit `backend/app/models/domain/meeting_note.py`. Find the recording metadata block:

```python
    # Recording metadata
    recording_path: Optional[str] = None
    recording_mode: Optional[RecordingMode] = None
    duration_seconds: Optional[int] = None
    transcript_lines: List[TranscriptLine] = Field(default_factory=list)
```

Replace with:

```python
    # Recording metadata
    recording_path: Optional[str] = None
    recording_mode: Optional[RecordingMode] = None
    duration_seconds: Optional[int] = None
    transcript_lines: List[TranscriptLine] = Field(default_factory=list)
    # Set when the note was populated from a URL (YouTube / podcast / video).
    source_url: Optional[str] = None
```

- [ ] **Step 6: Update `_to_orm` / `_to_domain`**

Edit `backend/app/services/notes_service.py`. In `_to_orm`, find the `recording_*` fields block and add `source_url` after `duration_seconds`:

```python
            recording_path=note.recording_path,
            recording_mode=note.recording_mode,
            duration_seconds=note.duration_seconds,
            source_url=note.source_url,
            transcript_lines=[l.model_dump() for l in note.transcript_lines],
```

In `_to_domain`, same insertion point:

```python
            recording_path=row.recording_path,
            recording_mode=row.recording_mode,
            duration_seconds=row.duration_seconds,
            source_url=row.source_url,
            transcript_lines=[
                TranscriptLine(**l) for l in (row.transcript_lines or [])
            ],
```

- [ ] **Step 7: Run existing tests to confirm nothing broke**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_live_transcription_parse.py backend/tests/integration/test_notes_ux_variant.py -v
```

Expected: all 11 tests still pass (no new failures from the `source_url` addition because Pydantic defaults the field to `None`).

- [ ] **Step 8: Migrate the dev DB**

Run (from repo root):

```bash
python -c "
import sqlite3
conn = sqlite3.connect('alphagraph.db')
cur = conn.cursor()
existing = {row[1] for row in cur.execute('PRAGMA table_info(meeting_notes)').fetchall()}
if 'source_url' not in existing:
    cur.execute('ALTER TABLE meeting_notes ADD COLUMN source_url VARCHAR')
    print('added source_url')
else:
    print('source_url already exists')
conn.commit()
conn.close()
"
```

Expected: `added source_url`.

- [ ] **Step 9: No commit yet** — per the plan preface, all commits consolidated at the end of Task 9.

---

## Task 2: VTT parser + caption fetch helper

**Files:**
- Create: `backend/app/services/url_ingest_service.py`
- Create: `backend/tests/unit/test_url_ingest_service.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_url_ingest_service.py`:

```python
"""
Unit tests for url_ingest_service.

yt-dlp itself is not exercised here (would hit the network). We test:
  - The VTT parser (pure-function, no network).
  - The caption-fetch happy path and not-found path (yt-dlp mocked).
  - The orchestration branching (captions path vs audio path — yt-dlp + gemini mocked).

Audio download is not unit-tested; it's a thin wrapper over yt-dlp and is
covered by the Task 9 smoke test with a real URL.
"""

from unittest.mock import patch

import pytest

from backend.app.services.url_ingest_service import (
    _parse_vtt,
    try_fetch_manual_captions,
)


SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:05.000 --> 00:00:10.000
Welcome to our Q1 earnings call.

00:00:10.000 --> 00:00:15.000
Our revenue was $2.1B, up 20% year-over-year.

00:00:15.500 --> 00:00:19.000
Management reaffirmed full-year guidance.
"""


def test_parse_vtt_returns_segments_with_timestamps():
    segments = _parse_vtt(SAMPLE_VTT)
    assert len(segments) == 3
    assert segments[0]["timestamp"] == "00:05"
    assert segments[0]["text_original"] == "Welcome to our Q1 earnings call."
    assert segments[1]["timestamp"] == "00:10"
    assert segments[1]["text_original"] == "Our revenue was $2.1B, up 20% year-over-year."
    assert segments[2]["timestamp"] == "00:15"


def test_parse_vtt_empty_returns_empty_list():
    assert _parse_vtt("WEBVTT\n\n") == []


def test_parse_vtt_handles_multi_line_cues():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:05.000
First line of cue.
Second line of cue.

00:00:05.000 --> 00:00:09.000
Next cue.
"""
    segments = _parse_vtt(vtt)
    assert len(segments) == 2
    assert segments[0]["text_original"] == "First line of cue. Second line of cue."
    assert segments[1]["text_original"] == "Next cue."


def test_parse_vtt_ignores_cue_identifiers_and_styling():
    """Some VTT files include a cue ID line and <c.class> inline styling. Both should be stripped."""
    vtt = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
<c.colorBBBBBB>Hello <b>world</b>.</c>
"""
    segments = _parse_vtt(vtt)
    assert len(segments) == 1
    assert segments[0]["text_original"] == "Hello world."


def test_try_fetch_manual_captions_returns_none_when_yt_dlp_finds_nothing():
    """yt_dlp.YoutubeDL().extract_info returns an info dict with an empty 'subtitles' key."""
    fake_info = {"subtitles": {}, "automatic_captions": {"en": [{"ext": "vtt", "url": "http://..."}]}}

    class FakeYDL:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def extract_info(self, url, download=False): return fake_info

    with patch("backend.app.services.url_ingest_service.yt_dlp.YoutubeDL", FakeYDL):
        result = try_fetch_manual_captions("http://youtube.com/watch?v=x", "auto")
    assert result is None


def test_try_fetch_manual_captions_returns_segments_when_manual_subs_present():
    fake_info = {
        "subtitles": {
            "en": [
                {"ext": "json3", "url": "http://example.com/en.json3"},
                {"ext": "vtt", "url": "http://example.com/en.vtt"},
            ]
        }
    }

    class FakeYDL:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def extract_info(self, url, download=False): return fake_info

    class FakeResp:
        status_code = 200
        text = SAMPLE_VTT

    def fake_get(url, timeout=None):
        return FakeResp()

    with patch("backend.app.services.url_ingest_service.yt_dlp.YoutubeDL", FakeYDL), \
         patch("backend.app.services.url_ingest_service.requests.get", fake_get):
        result = try_fetch_manual_captions("http://youtube.com/watch?v=x", "auto")

    assert result is not None
    assert result["language"] == "en"
    assert len(result["segments"]) == 3
    assert result["segments"][0]["text_original"].startswith("Welcome")
```

- [ ] **Step 2: Run tests to confirm they fail**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_url_ingest_service.py -v
```

Expected: ImportError on the whole module — `url_ingest_service` doesn't exist yet.

- [ ] **Step 3: Create `url_ingest_service.py` with VTT parser + caption fetcher**

Create `backend/app/services/url_ingest_service.py`:

```python
"""
URL ingest service — populate a MeetingNote from a YouTube / podcast / video URL.

Captions-first, audio-fallback:
  1. If yt-dlp reports manual captions in a supported language, download the
     VTT and feed the resulting text to gemini_polish_text (fast + cheap).
  2. Otherwise, download audio via yt-dlp and run the existing
     gemini_batch_transcribe (existing path, slower + a Gemini audio call).

Auto-generated captions are intentionally skipped — quality is too unreliable
for analyst workflows; the audio fallback is cheap enough.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional

import requests
import yt_dlp

logger = logging.getLogger(__name__)

# Languages we accept from the subtitles dict, in preference order.
_ACCEPTED_LANGS = ("en", "ja", "zh", "zh-Hans", "zh-Hant", "ko")


# ---------------------------------------------------------------------------
# VTT parsing
# ---------------------------------------------------------------------------

_VTT_TIMESTAMP = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{1,2}:\d{2}:\d{2}\.\d{3})"
)

# Strip simple HTML/tagged styling like <c.colorBBBBBB>, <b>, <i>, etc.
_VTT_TAG = re.compile(r"<[^>]+>")


def _hms_to_mmss(hms: str) -> str:
    """Convert 'HH:MM:SS.mmm' to 'MM:SS' (dropping milliseconds), collapsing
    the hour into minutes if non-zero."""
    try:
        hh, mm, rest = hms.split(":")
        ss = rest.split(".")[0]
        total_min = int(hh) * 60 + int(mm)
        return f"{total_min:02d}:{int(ss):02d}"
    except Exception:
        return hms


def _parse_vtt(vtt_text: str) -> list[dict]:
    """Parse a WEBVTT payload into the segment shape that the rest of the
    ingest pipeline expects: [{timestamp, speaker, text_original, text_english}].

    We leave text_english empty — if the caller wants bilingual output, it
    passes the segments through gemini_polish_text which will populate
    text_english via translation."""
    segments: list[dict] = []
    lines = vtt_text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        i += 1

        m = _VTT_TIMESTAMP.match(line)
        if not m:
            continue

        ts = _hms_to_mmss(m.group("start"))
        # Collect all non-empty lines until a blank (end of cue).
        buf: list[str] = []
        while i < n:
            cue_line = lines[i].rstrip()
            if not cue_line.strip():
                break
            buf.append(_VTT_TAG.sub("", cue_line).strip())
            i += 1

        text = " ".join(x for x in buf if x).strip()
        if text:
            segments.append({
                "timestamp": ts,
                "speaker": "",
                "text_original": text,
                "text_english": "",
            })

    return segments


# ---------------------------------------------------------------------------
# yt-dlp wrappers
# ---------------------------------------------------------------------------

def try_fetch_manual_captions(url: str, lang_hint: str = "auto") -> Optional[dict]:
    """Check whether the video has **manual** (creator-uploaded) captions in
    an accepted language. If so, download the VTT and parse it.

    Returns {"language": str, "segments": list[dict]} or None.

    NEVER returns auto-generated captions (Q1=b per user decision).
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("yt-dlp manual-captions probe failed for %s: %s", url, exc)
        return None

    subs = (info or {}).get("subtitles") or {}
    if not subs:
        return None

    # Pick a language: honour lang_hint if present, else scan accepted langs in order.
    candidates = []
    if lang_hint and lang_hint != "auto":
        candidates.append(lang_hint)
    candidates.extend(_ACCEPTED_LANGS)

    picked_lang: Optional[str] = None
    picked_entries: list[dict] = []
    for cand in candidates:
        # Match exact or with prefix (e.g. "en-US" when we asked for "en").
        for lang_key, entries in subs.items():
            if lang_key == cand or lang_key.startswith(f"{cand}-"):
                picked_lang = lang_key
                picked_entries = entries or []
                break
        if picked_lang:
            break

    if not picked_lang:
        return None

    # Prefer VTT, fall back to the first available format.
    vtt_entry = next((e for e in picked_entries if e.get("ext") == "vtt"), None)
    entry = vtt_entry or (picked_entries[0] if picked_entries else None)
    if not entry or not entry.get("url"):
        return None

    try:
        resp = requests.get(entry["url"], timeout=30)
        if resp.status_code != 200:
            logger.warning("Manual-captions download failed: HTTP %d", resp.status_code)
            return None
        segments = _parse_vtt(resp.text)
    except Exception as exc:
        logger.warning("Manual-captions fetch/parse failed: %s", exc)
        return None

    if not segments:
        return None

    # Normalise language to 2-letter ISO for the rest of the pipeline.
    norm_lang = picked_lang.split("-")[0].lower()
    return {"language": norm_lang, "segments": segments}


def download_audio(url: str, out_path: Path) -> str:
    """Download the audio track of `url` to an OPUS file at `out_path`
    (the caller provides the path WITHOUT extension; yt-dlp appends .opus).
    Returns the final path including extension.

    Raises `RuntimeError` if the download fails.
    """
    # yt-dlp will write `{out_path}.opus`.
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(out_path),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "opus",
            "preferredquality": "48",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise RuntimeError(f"yt-dlp audio download failed: {exc}") from exc

    final_path = f"{out_path}.opus"
    if not Path(final_path).exists():
        raise RuntimeError(f"yt-dlp claimed success but {final_path} is missing")
    return final_path


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[str], None]


def ingest_url(
    url: str,
    note_id: str,
    language_hint: str,
    audio_dir: Path,
    progress: ProgressCallback,
) -> dict:
    """End-to-end URL ingest. Returns the same dict shape as
    gemini_batch_transcribe (language, is_bilingual, key_topics, segments,
    summary, text, input_tokens, output_tokens)."""
    from backend.app.services.live_transcription import (
        gemini_batch_transcribe,
        gemini_polish_text,
    )

    progress("Checking for manual captions...")
    captions = try_fetch_manual_captions(url, language_hint)

    if captions:
        n = len(captions["segments"])
        lang = captions["language"]
        progress(f"Manual captions found ({lang}, {n} segments). Running Gemini polish...")
        result = gemini_polish_text(
            segments=captions["segments"],
            language_hint=lang,
            note_id=note_id,
        )
        return result

    progress("No manual captions. Downloading audio (this may take ~30s)...")
    out_stem = audio_dir / f"{note_id}_url"
    audio_path = download_audio(url, out_stem)

    progress("Audio downloaded. Running Gemini transcription (can take 1-5 min)...")
    final_lang = language_hint if language_hint in ("zh", "ja", "ko", "en") else "en"
    result = gemini_batch_transcribe(audio_path, final_lang, note_id)
    return result
```

- [ ] **Step 4: Run tests — confirm all 6 pass**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_url_ingest_service.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: No commit yet**

---

## Task 3: `gemini_polish_text` — text-input polish for the captions path

**Files:**
- Modify: `backend/app/services/live_transcription.py`
- Modify: `backend/tests/unit/test_live_transcription_parse.py`

- [ ] **Step 1: Write the failing test**

Edit `backend/tests/unit/test_live_transcription_parse.py`. At the bottom, add:

```python
# ---------------------------------------------------------------------------
# gemini_polish_text — text-input polish (URL ingest captions path)
# ---------------------------------------------------------------------------

def test_gemini_polish_text_returns_same_shape_as_audio_path():
    """gemini_polish_text produces a dict with the same keys that
    gemini_batch_transcribe returns, so the downstream pipeline doesn't care
    where the text came from. We mock the HTTP call so this runs offline."""
    from unittest.mock import patch
    from backend.app.services.live_transcription import gemini_polish_text

    # A canned Gemini response carrying a complete MeetingSummary.
    canned_response_json = {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "language": "en",
                "is_bilingual": False,
                "key_topics": ["test topic"],
                "segments": [{
                    "timestamp": "00:05",
                    "speaker": "",
                    "text_original": "Hello world.",
                    "text_english": "Hello world.",
                }],
                "summary": {
                    "storyline": "Short meeting.",
                    "key_points": [],
                    "all_numbers": [],
                    "recent_updates": [],
                    "financial_metrics": {"revenue": [], "profit": [], "orders": []},
                },
            })}]}
        }],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
    }

    class FakeResp:
        status_code = 200
        def json(self): return canned_response_json

    def fake_post(url, json=None, timeout=None):
        return FakeResp()

    input_segments = [
        {"timestamp": "00:05", "speaker": "", "text_original": "Hello world.", "text_english": ""},
    ]

    with patch("backend.app.services.live_transcription.requests.post", fake_post):
        result = gemini_polish_text(
            segments=input_segments,
            language_hint="en",
            note_id="test-note",
        )

    # Same keys as gemini_batch_transcribe returns.
    assert "language" in result
    assert "is_bilingual" in result
    assert "key_topics" in result
    assert "segments" in result
    assert "summary" in result
    assert "text" in result
    assert "input_tokens" in result
    assert "output_tokens" in result

    # Parsed values survived the round-trip.
    assert result["language"] == "en"
    assert result["key_topics"] == ["test topic"]
    assert len(result["segments"]) == 1
    assert result["summary"]["storyline"] == "Short meeting."


def test_gemini_polish_text_handles_no_api_key():
    """Degrades to empty shape when GEMINI_API_KEY is unset."""
    import os
    from backend.app.services.live_transcription import gemini_polish_text

    prior = os.environ.pop("GEMINI_API_KEY", None)
    try:
        result = gemini_polish_text(
            segments=[{"timestamp": "00:00", "speaker": "", "text_original": "x", "text_english": ""}],
            language_hint="en",
            note_id="test",
        )
    finally:
        if prior is not None:
            os.environ["GEMINI_API_KEY"] = prior

    assert "error" in result
    assert result["summary"]["storyline"] == ""
    assert result["segments"] == []
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_live_transcription_parse.py::test_gemini_polish_text_returns_same_shape_as_audio_path backend/tests/unit/test_live_transcription_parse.py::test_gemini_polish_text_handles_no_api_key -v
```

Expected: both fail with ImportError (`gemini_polish_text` not defined).

- [ ] **Step 3: Implement `gemini_polish_text`**

Edit `backend/app/services/live_transcription.py`. At the end of the file, AFTER `_flatten_segments_to_markdown`, add:

```python
def gemini_polish_text(
    segments: list[dict],
    language_hint: str = "en",
    note_id: str = "",
) -> dict:
    """
    Produce the full structured meeting-intelligence output from an already-
    transcribed text (typically captions extracted from a YouTube video).

    Input: segments in the same shape the rest of the pipeline uses, typically
    from _parse_vtt in url_ingest_service — {timestamp, speaker, text_original,
    text_english} (text_english can be blank; Gemini will fill it for non-EN).

    Output: the same dict shape gemini_batch_transcribe returns.
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "error": "GEMINI_API_KEY not set",
            "language": language_hint,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "summary": _empty_summary(),
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    lang_names = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}
    lang_name = lang_names.get(language_hint, "English")

    # Format the segments as a plain-text transcript that Gemini can reason over.
    transcript_lines = []
    for s in segments:
        ts = s.get("timestamp", "")
        text = s.get("text_original", "")
        if text:
            transcript_lines.append(f"[{ts}] {text}" if ts else text)
    transcript_text = "\n".join(transcript_lines)

    vocab_context = load_vocabulary(language_hint)

    prompt = f"""{vocab_context}
You are given a raw transcript extracted from subtitles / captions of a
financial meeting, interview, or conference talk. The captions may contain
minor errors (auto-generated or manually authored). Polish the transcript
AND produce a detailed analyst-grade summary.

Primary language: {lang_name} with possible English code-switching.

RAW TRANSCRIPT (timestamps in brackets):
{transcript_text[:30000]}

Return ONLY valid JSON matching this exact schema:
{{
  "language": "{language_hint}",
  "is_bilingual": true,
  "key_topics": ["topic1", "topic2", ...],
  "segments": [
    {{
      "timestamp": "MM:SS",
      "speaker": "speaker name or role if you can infer one, else empty string",
      "text_original": "the transcript segment in its primary language",
      "text_english": "English translation of this segment"
    }}
  ],
  "summary": {{
    "storyline": "1-2 paragraph narrative of how the meeting flowed, in English",
    "key_points": [
      {{
        "title": "short title (3-8 words)",
        "sub_points": [
          {{
            "text": "the sub-point itself, one sentence",
            "supporting": "2-3 sentence supporting argument grounded in what was said"
          }}
        ]
      }}
    ],
    "all_numbers": ["every numeric value mentioned with brief context"],
    "recent_updates": ["recent events / launches / partnerships / personnel changes"],
    "financial_metrics": {{
      "revenue": ["revenue-related mentions"],
      "profit": ["profit / margin / operating income mentions"],
      "orders": ["backlog / order book / bookings mentions"]
    }}
  }}
}}

Rules:
1. Preserve the original timestamps from the input. Adjust or merge only if two
   consecutive segments belong to the same thought.
2. Provide `text_english` for every segment. For English input, set
   `text_english` equal to `text_original` and `is_bilingual` to false.
3. NEVER fabricate numbers or quotes that weren't in the input. Summary fields
   must be grounded in the raw transcript.
4. Preserve financial terminology and proper nouns exactly as spoken.
5. Summary fields should be in English regardless of meeting language.
6. If the transcript is short or light on content, still produce storyline +
   key_points; it is OK for all_numbers / financial_metrics lists to be empty.
"""

    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    resp = requests.post(
        url,
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 65536,
                "responseMimeType": "application/json",
            },
        },
        timeout=600,
    )

    if resp.status_code != 200:
        return {
            "error": f"Gemini API error: {resp.status_code}",
            "language": language_hint,
            "is_bilingual": False,
            "key_topics": [],
            "segments": [],
            "summary": _empty_summary(),
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    result = resp.json()
    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
    usage = result.get("usageMetadata", {})

    parsed = _parse_polish_response(raw_text)
    text_md = _flatten_segments_to_markdown(parsed["segments"], parsed["is_bilingual"]) \
        if parsed["segments"] else parsed.get("text_markdown_fallback", "")

    return {
        "language": parsed["language"] or language_hint,
        "is_bilingual": parsed["is_bilingual"],
        "key_topics": parsed["key_topics"],
        "segments": parsed["segments"],
        "summary": parsed["summary"],
        "text": text_md,
        "input_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }
```

- [ ] **Step 4: Run the two new tests — confirm they pass**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_live_transcription_parse.py -v
```

Expected: all tests in that file pass (the two new ones plus the prior 8).

- [ ] **Step 5: Raise timeout on the audio path to 60 minutes**

Edit `backend/app/services/live_transcription.py`. Find the existing timeout in `gemini_batch_transcribe`:

```python
        timeout=900,
    )
```

Replace with:

```python
        timeout=3600,  # 60 min — covers long earnings calls and podcasts (Q3 from plan)
    )
```

- [ ] **Step 6: No commit yet**

---

## Task 4: WebSocket endpoint for URL ingest

**Files:**
- Modify: `backend/app/api/routers/v1/notes.py`

- [ ] **Step 1: Add the new WebSocket handler**

Edit `backend/app/api/routers/v1/notes.py`. At the very bottom of the file (after `_run_live_v2_session`), add:

```python
# ---------------------------------------------------------------------------
# URL Ingest — populate a note from a YouTube / podcast / video URL
# ---------------------------------------------------------------------------

@router.websocket("/ws/ingest-url/{note_id}")
async def ingest_url_websocket(
    websocket: WebSocket,
    note_id: str,
    url: str = Query(...),
    language: str = Query(default="auto"),
):
    """
    Stream URL-ingest progress + final polished transcript to the client.

    Protocol (identical to the live_v2 recording flow where overlapping):
      server -> client: {type: "status", message: str}
      server -> client: {type: "polished_transcript", text, language,
                         is_bilingual, key_topics, segments, summary,
                         input_tokens, output_tokens}
      server -> client: {type: "status", status: "complete", message: str}
      server -> client: {type: "error", message: str}
    """
    await websocket.accept()

    import asyncio
    from datetime import datetime
    import logging
    from backend.app.services.url_ingest_service import ingest_url

    logger = logging.getLogger("ingest_url_ws")

    # Re-use the audio dir the recording path uses so we don't sprawl.
    audio_dir = Path(__file__).resolve().parents[5] / "tools" / "audio_recorder" / "recordings"
    audio_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_event_loop()
    progress_queue: asyncio.Queue = asyncio.Queue()

    def progress_cb(message: str) -> None:
        """Called from the worker thread — hands status strings to the event loop."""
        loop.call_soon_threadsafe(progress_queue.put_nowait, message)

    async def drain_progress_until(done_event: asyncio.Event):
        """Forward any queued progress messages to the client until the worker signals done."""
        while not done_event.is_set() or not progress_queue.empty():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                await websocket.send_json({"type": "status", "message": msg})
            except asyncio.TimeoutError:
                continue

    try:
        done = asyncio.Event()
        result_holder: dict = {}
        error_holder: dict = {}

        def worker():
            try:
                result_holder["result"] = ingest_url(
                    url=url,
                    note_id=note_id,
                    language_hint=language,
                    audio_dir=audio_dir,
                    progress=progress_cb,
                )
            except Exception as exc:
                logger.exception("URL ingest failed")
                error_holder["error"] = str(exc)
            finally:
                loop.call_soon_threadsafe(done.set)

        # Run ingest + forward progress concurrently.
        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        drain_task = asyncio.create_task(drain_progress_until(done))

        await worker_task
        await drain_task

        if "error" in error_holder:
            await websocket.send_json({"type": "error", "message": error_holder["error"]})
            return

        result = result_holder.get("result") or {}
        if result.get("error"):
            await websocket.send_json({"type": "error", "message": result["error"]})
            return

        # Persist polished transcript + summary + source_url.
        from backend.app.db.session import SessionLocal
        db2 = SessionLocal()
        try:
            svc = NotesService(db2)
            svc.save_polished_transcript(
                note_id=note_id,
                tenant_id=TENANT_ID,
                markdown=result.get("text", ""),
                language=result.get("language", language),
                meta={
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "model": "gemini-2.5-flash",
                    "ran_at": datetime.utcnow().isoformat(),
                    "is_bilingual": result.get("is_bilingual", False),
                    "key_topics": result.get("key_topics", []),
                    "segments": result.get("segments", []),
                    "summary": result.get("summary") or {},
                    "source_url": url,
                },
            )
            # Also set source_url + summary_status directly on the note.
            svc.update_note(note_id, TENANT_ID, source_url=url)
        finally:
            db2.close()

        # Send the polished_transcript message — same shape as the recording
        # flow, so the frontend reuses its existing polished-transcript handling.
        await websocket.send_json({
            "type": "polished_transcript",
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "is_bilingual": result.get("is_bilingual", False),
            "key_topics": result.get("key_topics", []),
            "segments": result.get("segments", []),
            "summary": result.get("summary") or {},
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        })
        await websocket.send_json({
            "type": "status", "status": "complete",
            "message": "URL ingest complete.",
        })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("URL ingest WS error")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
```

- [ ] **Step 2: Extend `NotesService.update_note` to accept `source_url`**

The existing signature accepts `recording_path` but not `source_url`. Edit `backend/app/services/notes_service.py`:

Find:

```python
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
        recording_path: Optional[str] = None,
    ) -> Optional[MeetingNote]:
```

Replace with:

```python
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
        recording_path: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Optional[MeetingNote]:
```

And in the same function body, find:

```python
        if recording_path is not None:
            row.recording_path = recording_path
        row.updated_at = datetime.utcnow()
```

Replace with:

```python
        if recording_path is not None:
            row.recording_path = recording_path
        if source_url is not None:
            row.source_url = source_url
        row.updated_at = datetime.utcnow()
```

- [ ] **Step 3: Restart check — make sure the app still imports cleanly**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -c "from backend.app.api.routers.v1.notes import router; print('routes:', [r.path for r in router.routes if hasattr(r, 'path')][-5:])"
```

Expected: a list that includes `/ws/ingest-url/{note_id}` at the end.

- [ ] **Step 4: No commit yet**

---

## Task 5: Frontend — types + WS URL helper

**Files:**
- Modify: `frontend/src/lib/api/notesClient.ts`

- [ ] **Step 1: Add `source_url` to `NoteStub`**

Edit `frontend/src/lib/api/notesClient.ts`. Find:

```typescript
  recording_path: string | null;
  recording_mode: string | null;
  duration_seconds: number | null;
```

Replace with:

```typescript
  recording_path: string | null;
  recording_mode: string | null;
  duration_seconds: number | null;
  source_url: string | null;
```

- [ ] **Step 2: Add the ingest-URL WebSocket helper**

Find the existing `recordingWsUrl` helper at the bottom of the `notesClient` object. Immediately after it, add:

```typescript
  ingestUrlWsUrl: (noteId: string, sourceUrl: string, language = "auto") => {
    const base = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
    const qs = new URLSearchParams({ url: sourceUrl, language });
    return `${base}/api/v1/notes/ws/ingest-url/${noteId}?${qs}`;
  },
```

- [ ] **Step 3: Type-check**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -20
```

Expected: clean (only the pre-existing Next 15 noise filtered out).

- [ ] **Step 4: No commit yet**

---

## Task 6: Frontend — `UrlIngestModal` component

**Files:**
- Create: `frontend/src/components/domain/notes/UrlIngestModal.tsx`

- [ ] **Step 1: Create the modal**

Create `frontend/src/components/domain/notes/UrlIngestModal.tsx`:

```tsx
"use client";

/**
 * UrlIngestModal — top-bar-triggered modal that populates the note from a
 * YouTube / podcast / video URL. Opens a WebSocket to the backend ingest
 * endpoint, streams status messages, and on `polished_transcript` calls the
 * same onComplete callback the live recording uses. The parent container
 * wires onComplete to the existing handleRecordingComplete so the 4
 * editor-section insert logic is reused verbatim.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { X, Link2, Loader2, Sparkles, Globe } from "lucide-react";
import {
  notesClient,
  type TranscriptLine,
  type PolishedSegment,
  type MeetingSummary,
} from "@/lib/api/notesClient";

const LANGUAGES = [
  { value: "auto", label: "Auto-Detect" },
  { value: "en",   label: "English" },
  { value: "zh",   label: "Chinese" },
  { value: "ja",   label: "Japanese" },
  { value: "ko",   label: "Korean" },
];

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
      summary: MeetingSummary | null;
    } | null,
    sourceUrl: string,
  ) => void;
}

export default function UrlIngestModal({ noteId, onClose, onComplete }: Props) {
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("auto");
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [statusLog, setStatusLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    return () => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close();
      }
    };
  }, []);

  const addStatus = useCallback((msg: string) => {
    setStatusLog((prev) => [...prev, msg]);
  }, []);

  const handleStart = () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setError(null);
    setStatusLog([]);
    setStatus("running");

    const wsUrl = notesClient.ingestUrlWsUrl(noteId, trimmed, language);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => addStatus("Connecting...");

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "status") {
          if (typeof msg.message === "string") addStatus(msg.message);
          if (msg.status === "complete") {
            // handled by polished_transcript branch already — just log.
          }
        } else if (msg.type === "polished_transcript") {
          // Mirror the recording's onComplete shape, with no raw transcript
          // lines (URL ingest doesn't have a live-draft phase). Pass the
          // polished segments as the source of truth; the container will
          // insert them as the raw-transcript section as well.
          const polished = {
            segments: (Array.isArray(msg.segments) ? msg.segments : []) as PolishedSegment[],
            language: typeof msg.language === "string" ? msg.language : "",
            is_bilingual: Boolean(msg.is_bilingual),
            key_topics: Array.isArray(msg.key_topics) ? msg.key_topics : [],
            summary: msg.summary && typeof msg.summary === "object" ? (msg.summary as MeetingSummary) : null,
          };
          // Synthesise raw transcript lines from the polished segments so
          // the raw-transcript editor section still gets populated.
          const lines: TranscriptLine[] = polished.segments.map((s, idx) => ({
            line_id: idx + 1,
            timestamp: s.timestamp,
            speaker_label: s.speaker || "",
            speaker_name: null,
            text: s.text_original,
            is_flagged: false,
            is_interim: false,
          }));
          onComplete(lines, 0, polished, url.trim());
        } else if (msg.type === "error") {
          setError(typeof msg.message === "string" ? msg.message : "Unknown error");
          setStatus("error");
        }
      } catch {
        /* non-JSON */
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection failed.");
      setStatus("error");
    };

    ws.onclose = () => {
      wsRef.current = null;
    };
  };

  const isIdle = status === "idle";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget && isIdle) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden border border-slate-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-slate-50">
          <div className="flex items-center gap-2">
            <Link2 size={15} className="text-indigo-600" />
            <h3 className="text-sm font-semibold text-slate-900">Ingest from URL</h3>
          </div>
          <button onClick={onClose} disabled={!isIdle} className="p-1 text-slate-400 hover:text-slate-600 rounded-lg transition-colors disabled:opacity-40">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          {isIdle && (
            <>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
                  URL <span className="text-red-400">*</span>
                </label>
                <input
                  type="url"
                  autoFocus
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) handleStart(); }}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900 placeholder-slate-400"
                />
                <p className="mt-1 text-[10px] text-slate-400">
                  YouTube, Vimeo, SoundCloud, podcasts, direct MP3/MP4 links — anything yt-dlp supports.
                </p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1.5 uppercase tracking-wider">
                  Language
                </label>
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 text-slate-900"
                >
                  {LANGUAGES.map((l) => (
                    <option key={l.value} value={l.value}>{l.label}</option>
                  ))}
                </select>
                <p className="mt-1 text-[10px] text-slate-400">
                  Auto works well for most cases. Pick a specific language to bias caption lookup.
                </p>
              </div>

              <div className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-md p-2.5 space-y-1">
                <div className="flex items-center gap-1.5 font-semibold text-slate-700">
                  <Sparkles size={11} className="text-amber-500" />
                  How it works
                </div>
                <p>Tries manual captions first (fast, free). Falls back to audio download + Gemini if captions aren&apos;t available.</p>
              </div>
            </>
          )}

          {status === "running" && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-slate-700">
                <Loader2 size={14} className="animate-spin text-indigo-500" />
                <span>Processing…</span>
              </div>
              <div className="bg-slate-50 border border-slate-200 rounded-md p-2.5 max-h-48 overflow-y-auto space-y-1">
                {statusLog.map((s, i) => (
                  <p key={i} className="text-[11px] text-slate-600 font-mono leading-snug">
                    <span className="text-slate-400">•</span> {s}
                  </p>
                ))}
                {statusLog.length === 0 && (
                  <p className="text-[11px] text-slate-400">Waiting for first status message…</p>
                )}
              </div>
              <p className="text-[10px] text-slate-400 text-center">
                This can take 10 seconds (captions path) to a few minutes (audio path). Don&apos;t close the tab.
              </p>
            </div>
          )}

          {status === "error" && (
            <div className="space-y-3">
              <div className="px-3 py-2 bg-red-50 border border-red-200 rounded-md text-xs text-red-700">
                {error ?? "Ingest failed."}
              </div>
              {statusLog.length > 0 && (
                <details className="text-[11px] text-slate-500">
                  <summary className="cursor-pointer hover:text-slate-700">Show progress log</summary>
                  <div className="mt-1 bg-slate-50 border border-slate-200 rounded-md p-2 space-y-0.5">
                    {statusLog.map((s, i) => (
                      <p key={i} className="font-mono">• {s}</p>
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 bg-slate-50">
          {isIdle && (
            <>
              <button onClick={onClose} className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors">
                Cancel
              </button>
              <button
                onClick={handleStart}
                disabled={!url.trim()}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50 transition-colors shadow-sm"
              >
                <Globe size={14} />
                Extract Transcript
              </button>
            </>
          )}
          {status === "running" && (
            <span className="text-[11px] text-slate-400">Processing — modal will close automatically when done.</span>
          )}
          {status === "error" && (
            <button onClick={onClose} className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors">
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -20
```

Expected: clean.

- [ ] **Step 3: No commit yet**

---

## Task 7: Frontend — store, container, view wiring

**Files:**
- Modify: `frontend/src/app/(dashboard)/notes/[id]/store.ts`
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx`
- Modify: `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`

- [ ] **Step 1: Add modal state to the store**

Edit `frontend/src/app/(dashboard)/notes/[id]/store.ts`. Add a new state key alongside `showRecordingPopup`:

```typescript
interface NoteEditorStore {
  note: NoteStub | null;
  isSaving: boolean;
  isDirty: boolean;
  showRecordingPopup: boolean;
  showUrlIngestModal: boolean;
  setNote: (note: NoteStub) => void;
  clearNote: () => void;
  setSaving: (v: boolean) => void;
  setDirty: (v: boolean) => void;
  setShowRecordingPopup: (v: boolean) => void;
  setShowUrlIngestModal: (v: boolean) => void;
  patchNote: (partial: Partial<NoteStub>) => void;
}

export const useNoteEditorStore = create<NoteEditorStore>((set) => ({
  note: null,
  isSaving: false,
  isDirty: false,
  showRecordingPopup: false,
  showUrlIngestModal: false,

  setNote: (note) => set({ note, isDirty: false }),
  clearNote: () => set({ note: null, isDirty: false }),
  setSaving: (v) => set({ isSaving: v }),
  setDirty: (v) => set({ isDirty: v }),
  setShowRecordingPopup: (v) => set({ showRecordingPopup: v }),
  setShowUrlIngestModal: (v) => set({ showUrlIngestModal: v }),
  patchNote: (partial) =>
    set((s) => ({
      note: s.note ? { ...s.note, ...partial } : s.note,
      isDirty: true,
    })),
}));
```

- [ ] **Step 2: Wire container — add handleIngestComplete + modal toggles**

Edit `frontend/src/app/(dashboard)/notes/[id]/NotesEditorContainer.tsx`. Find the `useNoteEditorStore` destructuring near the top:

```typescript
  const {
    note, isSaving, isDirty, showRecordingPopup,
    setNote, clearNote, setSaving, setDirty, setShowRecordingPopup, patchNote,
  } = useNoteEditorStore();
```

Replace with:

```typescript
  const {
    note, isSaving, isDirty, showRecordingPopup, showUrlIngestModal,
    setNote, clearNote, setSaving, setDirty, setShowRecordingPopup,
    setShowUrlIngestModal, patchNote,
  } = useNoteEditorStore();
```

Then find the `handleRecordingComplete` callback. Immediately after it, add a handler specifically for URL ingest that sets `source_url` on the note and otherwise reuses the same insert logic:

```typescript
  // URL ingest: same output shape as recording, plus a source_url to persist.
  // We reuse the same editor-insert logic as recording by calling into the
  // existing handleRecordingComplete path — with the extra source_url write
  // and the URL ingest modal toggle.
  const handleUrlIngestComplete = useCallback(
    async (
      lines: TranscriptLine[],
      durationSeconds: number,
      polished: {
        segments: PolishedSegment[];
        language: string;
        is_bilingual: boolean;
        key_topics: string[];
        summary: MeetingSummary | null;
      } | null,
      sourceUrl: string,
    ) => {
      if (!note) return;

      // The backend already wrote source_url + polished_transcript + meta.
      // Refresh local state from the server so note.source_url is current.
      const fresh = await notesClient.get(note.note_id);
      if (fresh.success && fresh.data) {
        setNote(fresh.data);
        updateNote(fresh.data);
      }

      // Fall through to the same editor-insert flow as recording.
      if (editorRef.current) {
        const editor = editorRef.current;
        insertOrReplaceSection(editor, "user_notes", buildUserNotesHeadingNodes());
        if (polished && polished.summary) {
          insertOrReplaceSection(editor, "ai_summary", buildAISummarySectionNodes(polished.summary));
        }
        insertOrReplaceSection(editor, "raw_transcript", buildRawTranscriptSectionNodes(lines));
        if (polished && polished.segments.length > 0) {
          insertOrReplaceSection(
            editor,
            "polished_transcript",
            buildPolishedTranscriptSectionNodes(polished.segments, polished.is_bilingual),
          );
        }
      }

      setShowUrlIngestModal(false);
    },
    [note, setNote, updateNote, setShowUrlIngestModal],
  );
```

Then extend the view's props with the new handlers. Find the `return <NotesEditorView ... />` block and add:

```tsx
      onOpenUrlIngest={() => setShowUrlIngestModal(true)}
      onCloseUrlIngest={() => setShowUrlIngestModal(false)}
      onUrlIngestComplete={handleUrlIngestComplete}
      showUrlIngestModal={showUrlIngestModal}
```

after the existing `onCloseRecording={() => setShowRecordingPopup(false)}` line (so the final render is):

```tsx
  return (
    <NotesEditorView
      note={note}
      isSaving={isSaving}
      showRecordingPopup={showRecordingPopup}
      showUrlIngestModal={showUrlIngestModal}
      onBack={() => router.push("/notes")}
      onTitleChange={handleTitleChange}
      onContentChange={handleContentChange}
      onOpenRecording={() => setShowRecordingPopup(true)}
      onCloseRecording={() => setShowRecordingPopup(false)}
      onRecordingComplete={handleRecordingComplete}
      onOpenUrlIngest={() => setShowUrlIngestModal(true)}
      onCloseUrlIngest={() => setShowUrlIngestModal(false)}
      onUrlIngestComplete={handleUrlIngestComplete}
      onSaveSpeakers={handleSaveSpeakers}
      onExtractTopics={handleExtractTopics}
      onDelta={handleDelta}
      onMarkComplete={handleMarkComplete}
      onStartAISummary={handleStartAISummary}
      onEditorReady={handleEditorReady}
    />
  );
}
```

- [ ] **Step 3: Add `[Ingest URL]` top-bar button + modal mount + source_url chip in View**

Edit `frontend/src/app/(dashboard)/notes/[id]/NotesEditorView.tsx`.

Near the existing domain-component imports, add:

```tsx
import UrlIngestModal from "@/components/domain/notes/UrlIngestModal";
import { Link2 } from "lucide-react";
```

Update the `Link2` entry — the existing lucide-react import already has several icons, extend it instead of a second import. Find:

```tsx
import { ArrowLeft, Mic, Save, CheckCircle, Sparkles } from "lucide-react";
```

Replace with:

```tsx
import { ArrowLeft, Mic, Save, CheckCircle, Sparkles, Link2 } from "lucide-react";
```

(…and drop the separate `import { Link2 } from "lucide-react"` if you added it above.)

Extend the `Props` interface with the new handlers:

```tsx
  showUrlIngestModal: boolean;
  onOpenUrlIngest: () => void;
  onCloseUrlIngest: () => void;
  onUrlIngestComplete: (
    lines: TranscriptLine[],
    durationSeconds: number,
    polished: {
      segments: PolishedSegment[];
      language: string;
      is_bilingual: boolean;
      key_topics: string[];
      summary: MeetingSummary | null;
    } | null,
    sourceUrl: string,
  ) => void;
```

Update the default-exported function signature to destructure these:

```tsx
export default function NotesEditorView({
  note, isSaving, showRecordingPopup, showUrlIngestModal,
  onBack, onTitleChange, onContentChange,
  onOpenRecording, onCloseRecording, onRecordingComplete,
  onOpenUrlIngest, onCloseUrlIngest, onUrlIngestComplete,
  onSaveSpeakers, onExtractTopics, onDelta, onMarkComplete, onStartAISummary,
  onEditorReady,
}: Props) {
```

Find the top-bar right-side button cluster (the `<div className="flex items-center gap-3 shrink-0">` block that contains the Save indicator, AI Summary button, and Record Audio button). Add an `[Ingest URL]` button right before `[Record Audio]`:

```tsx
          {/* Ingest URL button */}
          <button
            onClick={onOpenUrlIngest}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-md hover:bg-indigo-100 transition-colors"
          >
            <Link2 size={13} />
            Ingest URL
          </button>
```

Add a "Ingested from …" chip under the title bar — find the top-bar block just after the existing note-type badge and meeting-date:

```tsx
          {/* Note type badge */}
          <span className={`px-2 py-0.5 text-[10px] font-medium rounded-full ${NOTE_TYPE_COLORS[note.note_type] ?? "bg-slate-100 text-slate-500"}`}>
            {NOTE_TYPE_LABELS[note.note_type] ?? note.note_type}
          </span>
```

Immediately after this span add:

```tsx
          {/* Ingested-from chip */}
          {note.source_url && (
            <a
              href={note.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium bg-slate-50 text-slate-600 border border-slate-200 rounded-full hover:bg-slate-100 hover:text-indigo-700 transition-colors max-w-[220px] truncate"
              title={note.source_url}
            >
              <Link2 size={10} />
              <span className="truncate">Ingested from URL</span>
            </a>
          )}
```

Finally, mount the modal. At the very bottom of the component JSX, just before the closing outer `</div>`, add:

```tsx
      {showUrlIngestModal && (
        <UrlIngestModal
          noteId={note.note_id}
          onClose={onCloseUrlIngest}
          onComplete={onUrlIngestComplete}
        />
      )}
```

- [ ] **Step 4: Type-check**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -30
```

Expected: clean.

- [ ] **Step 5: No commit yet**

---

## Task 8: Backend + frontend smoke (type + unit-test final pass)

**Files:** none modified.

- [ ] **Step 1: Run all backend tests**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && python -m pytest backend/tests/unit/test_live_transcription_parse.py backend/tests/unit/test_url_ingest_service.py backend/tests/integration/test_notes_ux_variant.py -v
```

Expected: all tests PASS — 11 prior + 2 new `gemini_polish_text` + 6 new URL-ingest = 19 passing.

- [ ] **Step 2: Final type-check**

Run:

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new/frontend" && npx tsc --noEmit 2>&1 | grep -v "\.next/types" | head -20
```

Expected: only pre-existing Next 15 params-Promise noise.

- [ ] **Step 3: No commit yet**

---

## Task 9: Manual smoke test + single clean commit

**Files:** none modified (verification only); commit at end.

- [ ] **Step 1: Restart backend + frontend dev servers**

Backend:

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Captions-path smoke test (fast)**

Open the notes library, create a new Variant-A note (or pick an existing one with no recording yet). In the top bar click **Ingest URL**. Paste a YouTube URL that you know has **manual captions** (any major conference talk, earnings call upload, or a channel that uploads subtitles — TED talks are a good safe bet). Pick language = Auto. Click **Extract Transcript**.

Confirm:
  - [ ] Modal status log shows `Checking for manual captions...` then `Manual captions found (en, N segments). Running Gemini polish...` then `URL ingest complete.`
  - [ ] Modal closes, editor shows 4 sections: `## Your Notes`, `## AI Summary` (storyline + key points etc.), `## Raw Live Transcript` (filled from caption segments, single-column since English), `## Polished Transcript`.
  - [ ] Top bar shows an "Ingested from URL" chip linking back to the source video.
  - [ ] Reload the page — everything persists.

- [ ] **Step 3: Audio-path smoke test (slower, only if captions path is green)**

Pick a short (~5 min) YouTube video that you know does NOT have manual captions — a small channel talking about finance, a short podcast episode on YouTube, etc. Create a new note, click **Ingest URL**, paste, submit.

Confirm:
  - [ ] Status log shows `No manual captions. Downloading audio (this may take ~30s)...` then `Audio downloaded. Running Gemini transcription (can take 1-5 min)...` then completion.
  - [ ] Editor fills with the same 4 sections.
  - [ ] Top bar shows the "Ingested from URL" chip.

- [ ] **Step 4: Error-path smoke test**

Click **Ingest URL**, paste a garbage URL like `https://example.com/does-not-exist.mp4`. Submit.

Confirm:
  - [ ] Status log shows the captions-check line, then transitions to the audio-download line.
  - [ ] Modal transitions to the error state with a readable error message (yt-dlp / URL-resolution failure).
  - [ ] Clicking Close returns to the note. Editor is unchanged. No half-inserted sections.

- [ ] **Step 5: No-regression check — recording still works**

Verify Variant A and Variant B recording flows still work end-to-end (the changes in this plan are all additive, but worth a final sanity).

- [ ] **Step 6: Commit (single commit for the whole plan)**

```bash
git add \
  backend/requirements.txt \
  backend/app/models/orm/note_orm.py \
  backend/app/models/domain/meeting_note.py \
  backend/app/services/notes_service.py \
  backend/app/services/live_transcription.py \
  backend/app/services/url_ingest_service.py \
  backend/app/api/routers/v1/notes.py \
  backend/tests/unit/test_live_transcription_parse.py \
  backend/tests/unit/test_url_ingest_service.py \
  alphagraph.db \
  frontend/src/lib/api/notesClient.ts \
  frontend/src/components/domain/notes/UrlIngestModal.tsx \
  frontend/src/app/\(dashboard\)/notes/\[id\]/store.ts \
  frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorContainer.tsx \
  frontend/src/app/\(dashboard\)/notes/\[id\]/NotesEditorView.tsx \
  docs/superpowers/plans/2026-04-23-url-ingest-for-notes.md

git commit -m "$(cat <<'EOF'
feat(notes): ingest meeting content from URL (YouTube / podcast / video)

A new top-bar [Ingest URL] button lets the user paste a video or podcast URL
and populate the note with the same 4-section editor layout recording
produces. Captions-first path: if the URL has manual creator-uploaded
captions, we extract them via yt-dlp, feed to Gemini text-polish (fast, cheap)
for the structured summary, and finish. Otherwise we fall back to audio
download + the existing Gemini audio transcribe path. Auto-generated captions
are skipped (Q1=b from brainstorm: quality too unreliable for analyst work).

Progress is streamed over a new WebSocket endpoint mirroring the recording
flow, so the modal shows "Checking for manual captions..." -> "Manual captions
found (en, 127 segments). Running Gemini polish..." -> "URL ingest complete."

Backend:
- yt-dlp dependency
- source_url column on MeetingNote (ORM + domain + migration)
- New gemini_polish_text() reusing _parse_polish_response for the text path;
  audio-path timeout raised to 60 min
- New url_ingest_service with VTT parser, manual-captions fetcher, audio
  downloader, ingest_url orchestration (captions-first, audio-fallback)
- New WebSocket /ws/ingest-url/{note_id} mirroring the live_v2 pattern
- 8 new unit tests (VTT parser, caption fetch happy path / not-found /
  multi-line cues / inline styling, gemini_polish_text shape + missing key)

Frontend:
- NoteStub.source_url + ingestUrlWsUrl helper
- UrlIngestModal — URL + language form, WS status streaming, reuses the
  editor-insert path by calling onComplete with the same shape as recording
- Top-bar [Ingest URL] button, "Ingested from URL" chip under the title,
  store + container wiring
- URL ingest reuses insertOrReplaceSection for all 4 editor sections; zero
  new editor logic

Plan: docs/superpowers/plans/2026-04-23-url-ingest-for-notes.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Confirm commit**

```bash
cd "C:/Users/Sharo/AI_projects/AlphaGraph_new" && git log --oneline -3
```

Expected: the new commit at the top.

---

## Self-Review Checklist

**Spec coverage** (against the design confirmed in the preceding conversation):

- "Try captions first, use if found" → Task 2 (`try_fetch_manual_captions`) + Task 4 (orchestration).
- "Fall back to audio if no captions" → Task 2 (`download_audio`) + existing `gemini_batch_transcribe`.
- "Manual subs only, no auto-captions" → `writeautomaticsub=False` in Task 2 Step 3.
- "Top-bar button trigger" → Task 7 Step 3 (Ingest URL button next to Record Audio).
- "60-min timeout on audio path" → Task 3 Step 5.
- "WebSocket progress" → Task 4 (`@router.websocket("/ws/ingest-url/{note_id}")`).
- "Store `source_url` column" → Task 1 Steps 4-6 + chip in Task 7 Step 3.
- "Same editor layout as recording" → Task 7 Step 2 reuses `insertOrReplaceSection` for all 4 sections.

**Placeholder scan:** no TBDs / TODOs / "handle edge cases" / "similar to Task N" patterns. Every code block is complete and pasteable.

**Type / name consistency:**

- `try_fetch_manual_captions`, `download_audio`, `ingest_url` consistent between Task 2 module definition and Task 4 WS call site.
- `gemini_polish_text(segments, language_hint, note_id)` signature consistent between Task 3 definition + Task 3 tests + Task 2's `ingest_url` call.
- `source_url` column / field name consistent across ORM / domain / `_to_orm` / `_to_domain` / `update_note` / `NoteStub` / the WebSocket persistence in Task 4 / the chip in Task 7.
- `ingestUrlWsUrl(noteId, sourceUrl, language)` signature consistent between Task 5 helper + Task 6 call site.
- `onUrlIngestComplete` signature `(lines, durationSeconds, polished, sourceUrl)` consistent between Task 6 modal's `onComplete` prop + Task 7 container handler + Task 7 view Props.
- `showUrlIngestModal`, `setShowUrlIngestModal` used in store (Task 7 Step 1), container (Step 2), and view props (Step 3).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-23-url-ingest-for-notes.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, single commit at the end.

Which approach?
