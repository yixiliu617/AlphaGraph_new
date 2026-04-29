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


# Empirical: 30 min audio -> ~75 sec, 2 hr audio -> ~4 min wall-clock.
# Linear fit: duration*0.025 + 60. With smart-chunking running 27-min
# slices in parallel, longer audio amortizes better -- this stays
# slightly conservative for 1-2hr files.
_ETA_RATIO    = 0.025
_ETA_BASELINE = 60.0   # seconds


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


def extract_audio_to_opus(src_path: str | Path, dst_path: str | Path, duration_sec: float) -> None:
    """Extract the audio track of `src_path` to mono 16 kHz Opus at `dst_path`.

    Bitrate matches the gemini_batch_transcribe_smart normalization step:
      - 48 kbps for short audio (<40 min)
      - 24 kbps for long audio (>=40 min) -- VoIP-transparent for speech
    """
    bitrate = "48k" if duration_sec < 40 * 60 else "24k"
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src_path),
            "-vn",                       # drop video track
            "-ac", "1",                  # mono
            "-ar", "16000",              # 16 kHz
            "-c:a", "libopus",           # opus codec
            "-b:a", bitrate,
            "-application", "voip",      # speech-optimized opus mode
            str(dst_path),
        ],
        capture_output=True, timeout=1200,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg extract failed for {src_path!r}: "
            f"{proc.stderr.decode(errors='replace')[:500]}"
        )
