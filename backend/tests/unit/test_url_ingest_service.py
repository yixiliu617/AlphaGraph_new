"""
Unit tests for url_ingest_service.

yt-dlp itself is not exercised here (would hit the network). We test:
  - The VTT parser (pure-function, no network).
  - The caption-fetch happy path and not-found path (yt-dlp mocked).

Audio download is not unit-tested; it's a thin wrapper over yt-dlp and is
covered by the Task 9 smoke test with a real URL.
"""

from unittest.mock import patch

import pytest

from backend.app.services.url_ingest_service import (
    _parse_vtt,
    try_fetch_manual_captions,
)


SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:05.000 --> 00:00:10.000
Welcome to our Q1 earnings call.

00:00:10.000 --> 00:00:15.000
Our revenue was $2.1B, up 20% year-over-year.

00:00:15.500 --> 00:00:19.000
Management reaffirmed full-year guidance.
"""


def test_parse_vtt_returns_segments_with_timestamps():
    segments = _parse_vtt(SAMPLE_VTT)
    assert len(segments) == 3
    assert segments[0]["timestamp"] == "00:05"
    assert segments[0]["text_original"] == "Welcome to our Q1 earnings call."
    assert segments[1]["timestamp"] == "00:10"
    assert segments[1]["text_original"] == "Our revenue was $2.1B, up 20% year-over-year."
    assert segments[2]["timestamp"] == "00:15"


def test_parse_vtt_empty_returns_empty_list():
    assert _parse_vtt("WEBVTT\n\n") == []


def test_parse_vtt_handles_multi_line_cues():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:05.000
First line of cue.
Second line of cue.

00:00:05.000 --> 00:00:09.000
Next cue.
"""
    segments = _parse_vtt(vtt)
    assert len(segments) == 2
    assert segments[0]["text_original"] == "First line of cue. Second line of cue."
    assert segments[1]["text_original"] == "Next cue."


def test_parse_vtt_ignores_cue_identifiers_and_styling():
    """Some VTT files include a cue ID line and <c.class> inline styling. Both should be stripped."""
    vtt = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
<c.colorBBBBBB>Hello <b>world</b>.</c>
"""
    segments = _parse_vtt(vtt)
    assert len(segments) == 1
    assert segments[0]["text_original"] == "Hello world."


def test_try_fetch_manual_captions_returns_none_when_yt_dlp_finds_nothing():
    """yt_dlp.YoutubeDL().extract_info returns an info dict with an empty 'subtitles' key."""
    fake_info = {"subtitles": {}, "automatic_captions": {"en": [{"ext": "vtt", "url": "http://..."}]}}

    class FakeYDL:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def extract_info(self, url, download=False): return fake_info

    with patch("backend.app.services.url_ingest_service.yt_dlp.YoutubeDL", FakeYDL):
        result = try_fetch_manual_captions("http://youtube.com/watch?v=x", "auto")
    assert result is None


def test_try_fetch_manual_captions_returns_segments_when_manual_subs_present():
    fake_info = {
        "subtitles": {
            "en": [
                {"ext": "json3", "url": "http://example.com/en.json3"},
                {"ext": "vtt", "url": "http://example.com/en.vtt"},
            ]
        }
    }

    class FakeYDL:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def extract_info(self, url, download=False): return fake_info

    class FakeResp:
        status_code = 200
        text = SAMPLE_VTT

    def fake_get(url, timeout=None):
        return FakeResp()

    with patch("backend.app.services.url_ingest_service.yt_dlp.YoutubeDL", FakeYDL), \
         patch("backend.app.services.url_ingest_service.requests.get", fake_get):
        result = try_fetch_manual_captions("http://youtube.com/watch?v=x", "auto")

    assert result is not None
    assert result["language"] == "en"
    assert len(result["segments"]) == 3
    assert result["segments"][0]["text_original"].startswith("Welcome")
