"""Unit tests for the per-file batch runner.

We don't exercise the real Gemini pipeline -- we inject a stub
`transcribe_fn` that simulates the result so tests stay fast and
deterministic.

Tests use plain `asyncio.run` inside sync test functions to avoid a
hard dependency on pytest-asyncio.
"""
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def _make_save_fn():
    """Return (save_fn, fake_note) -- fake_note has the duck-typed shape
    build_note_docx expects so the runner's docx-write step succeeds."""
    fake_note = MagicMock()
    fake_note.note_id = "note-1"
    fake_note.title = "test"
    fake_note.meeting_date = None
    fake_note.company_tickers = []
    fake_note.polished_transcript_meta = {
        "language": "en",
        "is_bilingual": False,
        "segments": [{"timestamp": "00:01", "speaker": "", "text_original": "Hi"}],
        "audio_duration_sec": 10.0,
    }
    return MagicMock(return_value=fake_note), fake_note


def test_emits_scan_complete_first(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    save_fn, _ = _make_save_fn()
    events = asyncio.run(_drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=save_fn)))
    assert events[0].kind == EventKind.SCAN_COMPLETE
    assert events[0].data["queued_count"] == 1


def test_emits_file_start_and_done_per_file(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    save_fn, _ = _make_save_fn()
    events = asyncio.run(_drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=save_fn)))
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.FILE_START) == 2
    assert kinds.count(EventKind.FILE_DONE)  == 2
    assert kinds[-1] == EventKind.BATCH_DONE


def test_writes_docx_to_transcripts_subdir(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    save_fn, _ = _make_save_fn()
    asyncio.run(_drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=save_fn)))
    out = tmp_path / "transcripts" / "a_transcript.docx"
    assert out.exists()
    assert out.stat().st_size > 1000  # any real docx is > 1KB


def test_failure_in_one_file_does_not_abort_batch(tmp_path):
    scan = _make_scan(tmp_path, "good.mp3", "bad.mp3", "good2.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    save_fn, _ = _make_save_fn()

    def flaky(path, lang, _g, t):
        if "bad" in path:
            raise RuntimeError("simulated transcription failure")
        return _fake_transcribe_ok(path, lang, _g, t)

    events = asyncio.run(_drain(run_batch(scan, options, transcribe_fn=flaky, save_note_fn=save_fn)))
    kinds = [e.kind for e in events]
    assert kinds.count(EventKind.FILE_DONE)  == 2
    assert kinds.count(EventKind.FILE_ERROR) == 1
    assert kinds[-1] == EventKind.BATCH_DONE
    final = events[-1].data
    assert final["succeeded"] == 2
    assert final["failed"]    == 1


def test_batch_done_includes_skipped_count(tmp_path):
    scan = _make_scan(tmp_path, "a.mp3")
    scan.skipped.append(ScanSkip(name="b.mp3", reason="already_transcribed"))
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=1)
    save_fn, _ = _make_save_fn()
    events = asyncio.run(_drain(run_batch(scan, options, transcribe_fn=_fake_transcribe_ok, save_note_fn=save_fn)))
    final = events[-1]
    assert final.kind == EventKind.BATCH_DONE
    assert final.data["skipped"] == 1


# ---------------------------------------------------------------------------
# Concurrency + auto-throttle (Task 6)
# ---------------------------------------------------------------------------

import threading
import time


def test_concurrency_two_runs_two_at_once(tmp_path):
    """With concurrency=2 and 4 short files, at least 2 should be in flight
    at the same time."""
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3", "c.mp3", "d.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=2)
    save_fn, _ = _make_save_fn()

    inflight_now = {"v": 0}
    inflight_max = {"v": 0}
    lock = threading.Lock()

    def slow_fake(path, lang, _g, t):
        with lock:
            inflight_now["v"] += 1
            inflight_max["v"] = max(inflight_max["v"], inflight_now["v"])
        # asyncio.to_thread runs us on a worker thread; time.sleep lets
        # other workers proceed in parallel.
        time.sleep(0.05)
        with lock:
            inflight_now["v"] -= 1
        return _fake_transcribe_ok(path, lang, _g, t)

    asyncio.run(_drain(run_batch(scan, options,
                                 transcribe_fn=slow_fake,
                                 save_note_fn=save_fn)))
    assert inflight_max["v"] >= 2, f"expected >=2 in flight, saw {inflight_max['v']}"


def test_big_file_blocks_new_starts(tmp_path):
    """While a >90 min file is in flight, no NEW file starts. Files that
    were already in flight when the big file started can finish; that's
    the pragmatic semantic (killing in-flight work would be wasteful)."""
    # Put the huge file FIRST so nothing else has started yet when it does.
    scan = _make_scan(tmp_path, "huge.mp3", "small1.mp3", "small2.mp3")
    for f in scan.queued:
        if f.name == "huge.mp3":
            f.duration_sec = 95 * 60   # > 90 min threshold
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=2)
    save_fn, _ = _make_save_fn()

    huge_running = {"v": False}
    other_started_during_huge = {"v": False}
    lock = threading.Lock()

    def fake(path, lang, _g, t):
        with lock:
            if "huge" in path:
                huge_running["v"] = True
            elif huge_running["v"]:
                other_started_during_huge["v"] = True
        time.sleep(0.05)
        with lock:
            if "huge" in path:
                huge_running["v"] = False
        return _fake_transcribe_ok(path, lang, _g, t)

    asyncio.run(_drain(run_batch(scan, options,
                                 transcribe_fn=fake,
                                 save_note_fn=save_fn)))
    assert not other_started_during_huge["v"], (
        "no non-huge file should start while huge is in flight"
    )


def test_429_halves_concurrency_for_remaining_files(tmp_path):
    """A '429' error on file 1 halves the cap; remaining files run with the lower cap."""
    scan = _make_scan(tmp_path, "a.mp3", "b.mp3", "c.mp3", "d.mp3", "e.mp3")
    options = BatchOptions(translation_language="en", note_type="meeting", language=None, concurrency=4)
    save_fn, _ = _make_save_fn()

    inflight_now = {"v": 0}
    max_after_429 = {"v": 0}
    saw_429 = {"v": False}
    lock = threading.Lock()

    def fake(path, lang, _g, t):
        if "a.mp3" in path:
            raise RuntimeError("HTTP 429 too many requests")
        with lock:
            inflight_now["v"] += 1
            if saw_429["v"]:
                max_after_429["v"] = max(max_after_429["v"], inflight_now["v"])
        time.sleep(0.04)
        with lock:
            inflight_now["v"] -= 1
        return _fake_transcribe_ok(path, lang, _g, t)

    async def _drain_with_flag(agen):
        out = []
        async for ev in agen:
            if ev.kind == EventKind.FILE_ERROR and "429" in ev.data["error"]:
                saw_429["v"] = True
            out.append(ev)
        return out

    asyncio.run(_drain_with_flag(run_batch(scan, options,
                                            transcribe_fn=fake,
                                            save_note_fn=save_fn)))
    # Cap was 4; after 429 it should drop to 2 -> at most 2 concurrent.
    assert max_after_429["v"] <= 2, (
        f"expected cap halved after 429, saw {max_after_429['v']}"
    )
