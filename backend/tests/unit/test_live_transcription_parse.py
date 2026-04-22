"""
Unit tests for the structured-output parser in live_transcription.
We don't hit the Gemini API; we only verify our handling of the JSON shape.
"""

import json
import pytest

from backend.app.services.live_transcription import (
    _parse_polish_response,
    _flatten_segments_to_markdown,
)


SAMPLE_JA_JSON = {
    "language": "ja",
    "is_bilingual": True,
    "key_topics": ["revenue", "ARM"],
    "segments": [
        {
            "timestamp": "00:15",
            "speaker": "Tanaka (CFO)",
            "text_original": "売上高は前年比20%増となりました。",
            "text_english": "Revenue grew 20% year-over-year.",
        },
        {
            "timestamp": "00:32",
            "speaker": "Tanaka (CFO)",
            "text_original": "ARMとの提携は順調です。",
            "text_english": "Our ARM partnership is progressing well.",
        },
    ],
}


def test_parse_polish_response_happy_path():
    parsed = _parse_polish_response(json.dumps(SAMPLE_JA_JSON))
    assert parsed["language"] == "ja"
    assert parsed["is_bilingual"] is True
    assert parsed["key_topics"] == ["revenue", "ARM"]
    assert len(parsed["segments"]) == 2
    assert parsed["segments"][0]["text_english"] == "Revenue grew 20% year-over-year."


def test_parse_polish_response_falls_back_for_non_json():
    """If Gemini returns non-JSON (prompt drift), we degrade gracefully."""
    parsed = _parse_polish_response("This is just plain markdown, not JSON.")
    assert parsed["language"] == ""
    assert parsed["is_bilingual"] is False
    assert parsed["key_topics"] == []
    assert parsed["segments"] == []
    # Raw text preserved so the user still sees *something*.
    assert "plain markdown" in parsed["text_markdown_fallback"]


def test_flatten_segments_bilingual():
    md = _flatten_segments_to_markdown(SAMPLE_JA_JSON["segments"], is_bilingual=True)
    # Bilingual form renders as a markdown table with 3 columns.
    assert "| Time" in md
    assert "売上高は前年比20%増となりました。" in md
    assert "| Revenue grew 20% year-over-year. |" in md
    assert "00:15" in md and "00:32" in md


def test_flatten_segments_monolingual():
    segments = [
        {"timestamp": "00:10", "speaker": "Alice", "text_original": "Hello.", "text_english": "Hello."},
    ]
    md = _flatten_segments_to_markdown(segments, is_bilingual=False)
    # Monolingual form uses 2-column table; English column suppressed.
    assert "| Time" in md
    assert "| Text" in md
    assert "English" not in md
