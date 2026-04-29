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
            f"ffprobe failed for {audio_path!r}: "
            f"{proc.stderr.decode(errors='replace')[:200]}"
        )
    try:
        info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        return float(info["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"ffprobe gave no duration for {audio_path!r}: {exc}"
        )


def estimate_transcribe_seconds(duration_seconds: float) -> float:
    """ETA = max(0, duration * 0.4) + 30. Clamps negatives to 0 baseline."""
    body = max(0.0, float(duration_seconds)) * _ETA_RATIO
    return body + _ETA_BASELINE
