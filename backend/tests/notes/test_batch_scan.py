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
