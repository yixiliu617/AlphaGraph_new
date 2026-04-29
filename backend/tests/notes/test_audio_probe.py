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
    # Defensive -- negative duration should not produce a negative ETA.
    assert estimate_transcribe_seconds(-5.0) >= 0
