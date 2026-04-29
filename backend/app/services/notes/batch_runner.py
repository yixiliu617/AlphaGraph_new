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


async def run_batch(
    scan:          ScanResult,
    options:       BatchOptions,
    transcribe_fn: TranscribeFn,
    save_note_fn:  Callable[[ScanFile, dict, BatchOptions], Any],
) -> AsyncIterator[Event]:
    """Yield events for an entire batch (sequential -- Task 6 adds bounded
    concurrency + auto-throttle).

    Args:
      scan:          ScanResult from batch_scan.scan_folder
      options:       BatchOptions
      transcribe_fn: callable(path, lang, glossary, translation_lang) -> dict
                     (matches gemini_batch_transcribe_smart's signature)
      save_note_fn:  callable(scan_file, transcribe_result, options) -> note
                     The runner uses note.note_id and the duck-typed shape
                     build_note_docx expects.
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
