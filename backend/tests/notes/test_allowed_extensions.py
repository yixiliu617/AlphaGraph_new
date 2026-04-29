"""Verify the whitelist includes both audio and video extensions."""
from backend.app.api.routers.v1.notes import _ALLOWED_AUDIO_EXT


def test_audio_extensions_present():
    for ext in (".wav", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".aac", ".webm"):
        assert ext in _ALLOWED_AUDIO_EXT, f"missing audio ext {ext}"


def test_video_extensions_present():
    for ext in (".mp4", ".mov", ".mkv", ".avi", ".m4v"):
        assert ext in _ALLOWED_AUDIO_EXT, f"missing video ext {ext}"


def test_extensions_lowercase():
    for ext in _ALLOWED_AUDIO_EXT:
        assert ext == ext.lower(), f"non-lowercase ext {ext}"
        assert ext.startswith("."), f"ext must start with dot: {ext}"
