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


_BIG_FILE_SECONDS = 90 * 60       # 90 minutes -- drop concurrency to 1


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

    async def dispatcher():
        tasks: list[asyncio.Task] = []
        for index, sf in enumerate(scan.queued):
            # Reserve atomically before spawning the worker. Avoids the
            # race where the dispatcher checks "slot available" before the
            # previous worker has claimed its slot.
            while not await reserve_slot(sf):
                await asyncio.sleep(0.05)
            tasks.append(asyncio.create_task(worker(index, sf)))
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
