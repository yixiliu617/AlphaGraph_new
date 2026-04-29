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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from backend.app.services.notes.batch_scan import ScanResult, ScanFile
from backend.app.services.notes.docx_builder import build_note_docx
from backend.app.services.notes.audio_probe import extract_audio_to_opus
from backend.app.services.live_transcription import gemini_generate_interview_review


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
    generate_review:      bool = False    # interview-style AI review at top of docx


# Type alias: a transcribe function with the same signature as
# backend.app.services.live_transcription.gemini_batch_transcribe_smart.
TranscribeFn = Callable[[str, Optional[str], str, str], dict]


_BIG_FILE_SECONDS = 90 * 60       # 90 minutes -- drop concurrency to 1

# How often to emit a tick (FILE_PROGRESS event) while a long-running
# subprocess / Gemini call is in flight. Small enough that the user sees
# the bar move; large enough that we don't flood the SSE stream.
_TICK_INTERVAL_SEC = 3.0


def _fmt_elapsed(sec: float) -> str:
    s = int(sec)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


async def _ticking_progress(task: asyncio.Task, interval: float = _TICK_INTERVAL_SEC):
    """Yield monotonic elapsed-second floats every `interval` seconds while
    `task` is still running. Returns (cleanly) the moment the task finishes.

    Uses asyncio.shield so the outer wait_for timeout doesn't cancel the
    real worker -- we just want to peek at "is it done yet?" without
    interrupting it.
    """
    started = time.monotonic()
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            yield time.monotonic() - started


async def run_batch(
    scan:          ScanResult,
    options:       BatchOptions,
    transcribe_fn: TranscribeFn,
    save_note_fn:  Callable[[ScanFile, dict, BatchOptions], Any],
) -> AsyncIterator[Event]:
    """Yield events for an entire batch with bounded concurrency + auto-throttle.

    Concurrency policy:
      - cap starts at options.concurrency (clamped to [1, 4])
      - if any in-flight file has duration_sec > 90 min, the cap is
        effectively 1 (no new file starts until the big one finishes)
      - if any file errors with a 429-flavored message, cap halves (min 1)
        for the rest of the batch
    """
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

    out_q: asyncio.Queue = asyncio.Queue()
    inflight_durations: list[float] = []
    cap_box = {"value": cap}
    inflight_lock = asyncio.Lock()

    async def reserve_slot(sf: ScanFile) -> bool:
        """Atomically check capacity + record this file's duration. Caller
        must call release_slot when done. Returns False if no slot available."""
        async with inflight_lock:
            if len(inflight_durations) >= cap_box["value"]:
                return False
            if any(d > _BIG_FILE_SECONDS for d in inflight_durations):
                return False
            inflight_durations.append(sf.duration_sec)
            return True

    async def release_slot(sf: ScanFile):
        async with inflight_lock:
            inflight_durations.remove(sf.duration_sec)

    async def worker(index: int, sf: ScanFile):
        # Slot has already been reserved by the dispatcher.
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
            await release_slot(sf)

    # Track all spawned worker tasks so we can cancel them on early exit
    # (client disconnect / generator aclose).
    worker_tasks: list[asyncio.Task] = []

    async def dispatcher():
        for index, sf in enumerate(scan.queued):
            # Reserve atomically before spawning the worker. Avoids the
            # race where the dispatcher checks "slot available" before the
            # previous worker has claimed its slot.
            while not await reserve_slot(sf):
                await asyncio.sleep(0.05)
            worker_tasks.append(asyncio.create_task(worker(index, sf)))
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        await out_q.put(None)   # sentinel: dispatcher done

    dispatcher_task = asyncio.create_task(dispatcher())

    try:
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
    finally:
        # Either we finished normally, OR the consumer (HTTP layer) closed
        # the generator due to client disconnect. Either way, cancel any
        # still-pending workers so their Gemini calls stop wasting tokens.
        # NB: an in-flight asyncio.to_thread'd Gemini call won't be killed
        # mid-flight (Python threads aren't cancellable), but the asyncio
        # task waiting on it gets cancelled and the next file in queue
        # never starts.
        for t in worker_tasks:
            if not t.done():
                t.cancel()
        if not dispatcher_task.done():
            dispatcher_task.cancel()
        # Wait briefly for cancellations to settle. Any task that swallows
        # cancellation just gets its result discarded.
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
        try:
            await dispatcher_task
        except (asyncio.CancelledError, Exception):
            pass


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
        # Stage 1: pre-extract audio track to <folder>/audio/<stem>.opus.
        # We persist the opus file (rather than letting Gemini's pipeline
        # discard a temp copy) so the user can verify the extraction and
        # re-use it for future runs without re-encoding. ticks emit every
        # ~3s so the user sees the bar move during ffmpeg.
        audio_dir = Path(sf.path).parent / "audio"
        opus_path = audio_dir / f"{Path(sf.name).stem}.opus"
        if not opus_path.exists():
            yield Event(EventKind.FILE_PROGRESS, {
                "index": index, "name": sf.name, "percent": 3,
                "stage": "extracting audio (starting ffmpeg)",
            })
            extract_eta = max(10.0, sf.duration_sec * 0.03)   # ~30x realtime
            extract_task = asyncio.create_task(asyncio.to_thread(
                extract_audio_to_opus, sf.path, opus_path, sf.duration_sec,
            ))
            async for elapsed in _ticking_progress(extract_task):
                ratio = min(0.95, elapsed / extract_eta)
                yield Event(EventKind.FILE_PROGRESS, {
                    "index": index, "name": sf.name,
                    "percent": int(3 + ratio * 17),    # 3% -> 20%
                    "stage":   f"extracting audio ({_fmt_elapsed(elapsed)} elapsed, ~{_fmt_elapsed(extract_eta)} expected)",
                })
            extract_task.result()    # surface ffmpeg errors

        # Stage 2: Gemini transcription. Tick every ~3s with elapsed time
        # so the user knows it's still working during the multi-minute
        # call. The existing pipeline does its own normalization pass on
        # the opus input (cheap, a few seconds of CPU).
        yield Event(EventKind.FILE_PROGRESS, {
            "index": index, "name": sf.name, "percent": 22,
            "stage": "transcribing (calling Gemini)",
        })
        gemini_eta = max(30.0, sf.duration_sec * 0.025 + 30)
        gemini_task = asyncio.create_task(asyncio.to_thread(
            transcribe_fn, str(opus_path), options.language, "", options.translation_language,
        ))
        async for elapsed in _ticking_progress(gemini_task):
            ratio = min(0.95, elapsed / gemini_eta)
            yield Event(EventKind.FILE_PROGRESS, {
                "index": index, "name": sf.name,
                "percent": int(22 + ratio * 65),   # 22% -> 87%
                "stage":   f"transcribing ({_fmt_elapsed(elapsed)} elapsed, ~{_fmt_elapsed(gemini_eta)} expected)",
            })
        transcribe_result = gemini_task.result()
        if transcribe_result.get("error"):
            raise RuntimeError(f"transcription error: {transcribe_result['error']}")

        # Stage 3 (optional): generate interview-style review. Separate
        # Gemini call with audio + transcript text -- catches tone /
        # hesitation that the transcript alone doesn't preserve. Same
        # ticking pattern as the transcribe phase so the bar keeps moving.
        if options.generate_review:
            yield Event(EventKind.FILE_PROGRESS, {
                "index": index, "name": sf.name, "percent": 88,
                "stage": "generating interview review (calling Gemini)",
            })
            review_eta = max(20.0, sf.duration_sec * 0.012 + 20)
            transcript_for_review = transcribe_result.get("text") or ""
            review_task = asyncio.create_task(asyncio.to_thread(
                gemini_generate_interview_review, str(opus_path), transcript_for_review,
            ))
            async for elapsed in _ticking_progress(review_task):
                ratio = min(0.95, elapsed / review_eta)
                yield Event(EventKind.FILE_PROGRESS, {
                    "index": index, "name": sf.name,
                    "percent": int(88 + ratio * 4),    # 88% -> 92%
                    "stage":   f"generating review ({_fmt_elapsed(elapsed)} elapsed, ~{_fmt_elapsed(review_eta)} expected)",
                })
            review_result = review_task.result()
            if review_result.get("error"):
                # Don't fail the whole file if review fails -- log and
                # ship the docx without it.
                transcribe_result["interview_review_error"] = review_result["error"]
            else:
                transcribe_result["interview_review"] = review_result.get("review_markdown") or ""

        yield Event(EventKind.FILE_PROGRESS, {
            "index": index, "name": sf.name, "percent": 93,
            "stage": "saving note + building transcript .docx",
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
