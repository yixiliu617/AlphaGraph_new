"""Folder scan: list audio/video files, classify queued vs already-done,
disambiguate filename collisions, sort alphabetically.

Read-only -- does not mutate the filesystem. Output is what the SSE
endpoint emits as the `scan_complete` event.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from backend.app.services.notes.audio_probe import (
    probe_duration_seconds,
    estimate_transcribe_seconds,
)


# Parallelism cap for ffprobe during scan. ffprobe is I/O-light but spends
# most of its wall time waiting on disk seeks for the container metadata,
# so a small thread pool gives a big speedup for large MP4 folders.
_PROBE_WORKERS = 8


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

    # First pass: classify each candidate as skipped vs to-be-probed. We
    # don't probe inside this loop -- probing is parallelized below.
    to_probe: list[Path] = []
    for p in candidates:
        ext = p.suffix.lower()
        transcript_name = _transcript_name(p.stem, ext, stem_collisions)
        if (transcripts_dir / transcript_name).exists():
            skipped.append(ScanSkip(name=p.name, reason="already_transcribed"))
        else:
            to_probe.append(p)

    # Probe in parallel. ffprobe is mostly I/O so threads work well.
    def _safe_probe(path: Path) -> float:
        try:
            return probe_duration_seconds(str(path))
        except (RuntimeError, ValueError):
            return 0.0

    if to_probe:
        with ThreadPoolExecutor(max_workers=min(_PROBE_WORKERS, len(to_probe))) as ex:
            durations = list(ex.map(_safe_probe, to_probe))
    else:
        durations = []

    for p, dur in zip(to_probe, durations):
        ext = p.suffix.lower()
        queued.append(ScanFile(
            name            = p.name,
            path            = str(p),
            size_bytes      = p.stat().st_size,
            duration_sec    = dur,
            eta_sec         = estimate_transcribe_seconds(dur),
            transcript_name = _transcript_name(p.stem, ext, stem_collisions),
        ))

    return ScanResult(folder=str(folder), queued=queued, skipped=skipped)
