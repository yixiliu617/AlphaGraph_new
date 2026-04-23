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


# ---------------------------------------------------------------------------
# Summary parsing tests
# ---------------------------------------------------------------------------

SAMPLE_WITH_SUMMARY = {
    "language": "ja",
    "is_bilingual": True,
    "key_topics": ["ARM"],
    "segments": [
        {
            "timestamp": "00:15",
            "speaker": "Tanaka",
            "text_original": "売上高は $2.1B でした。",
            "text_english": "Revenue was $2.1B.",
        },
    ],
    "summary": {
        "storyline": "Management walked through Q1 performance and reaffirmed FY guidance.",
        "key_points": [
            {
                "title": "Revenue growth driven by ARM",
                "sub_points": [
                    {
                        "text": "Q1 revenue grew 20% YoY",
                        "supporting": "The CFO attributed the beat to ARM partnership shipments. Mix shift toward higher-ASP products added 3 pts. Management expects momentum to continue into Q2.",
                    }
                ],
            }
        ],
        "all_numbers": ["$2.1B Q1 revenue", "20% YoY growth", "42% gross margin"],
        "recent_updates": ["Closed the X acquisition last month", "Announced ARM partnership expansion"],
        "financial_metrics": {
            "revenue": ["Q1 revenue $2.1B, up 20% YoY"],
            "profit": ["Operating margin 28%, up 200bps YoY"],
            "orders": ["Backlog $8.5B, up 30% QoQ"],
        },
    },
}


def test_parse_polish_response_extracts_summary():
    parsed = _parse_polish_response(json.dumps(SAMPLE_WITH_SUMMARY))
    summary = parsed["summary"]
    assert summary["storyline"].startswith("Management walked")
    assert len(summary["key_points"]) == 1
    assert summary["key_points"][0]["title"] == "Revenue growth driven by ARM"
    assert len(summary["key_points"][0]["sub_points"]) == 1
    assert "ARM partnership shipments" in summary["key_points"][0]["sub_points"][0]["supporting"]
    assert "$2.1B Q1 revenue" in summary["all_numbers"]
    assert "Closed the X acquisition last month" in summary["recent_updates"]
    assert summary["financial_metrics"]["revenue"] == ["Q1 revenue $2.1B, up 20% YoY"]
    assert summary["financial_metrics"]["profit"] == ["Operating margin 28%, up 200bps YoY"]
    assert summary["financial_metrics"]["orders"] == ["Backlog $8.5B, up 30% QoQ"]


def test_parse_polish_response_missing_summary_returns_empty_shape():
    """If Gemini omits the summary object, we still return a complete empty structure."""
    no_summary = {k: v for k, v in SAMPLE_WITH_SUMMARY.items() if k != "summary"}
    parsed = _parse_polish_response(json.dumps(no_summary))
    summary = parsed["summary"]
    assert summary["storyline"] == ""
    assert summary["key_points"] == []
    assert summary["all_numbers"] == []
    assert summary["recent_updates"] == []
    assert summary["financial_metrics"] == {"revenue": [], "profit": [], "orders": []}


def test_parse_polish_response_non_json_includes_empty_summary():
    """Non-JSON fallback also carries a complete empty summary so downstream
    code never has to null-check summary fields."""
    parsed = _parse_polish_response("This is not JSON.")
    assert parsed["summary"] == {
        "storyline": "",
        "key_points": [],
        "all_numbers": [],
        "recent_updates": [],
        "financial_metrics": {"revenue": [], "profit": [], "orders": []},
    }


def test_parse_polish_response_repairs_truncated_json():
    """Gemini occasionally hits maxOutputTokens mid-output and truncates the
    JSON. Our repair path (json_repair) should recover everything up to the
    truncation point."""
    truncated = """{
      "language": "en",
      "is_bilingual": false,
      "key_topics": ["ARM", "AI"],
      "segments": [
        {"timestamp": "00:05", "speaker": "CEO", "text_original": "Hello.", "text_english": "Hello."}
      ],
      "summary": {
        "storyline": "Management walked through Q1.",
        "key_points": [
          {"title": "Point one", "sub_points": [{"text": "a", "supporting": "b"}]}
        ],
        "all_numbers": ["$2.1B", "42%", "17 tri"""
    parsed = _parse_polish_response(truncated)
    # Repair should recover the well-formed parts even though the last string was cut off.
    assert parsed["language"] == "en"
    assert parsed["key_topics"] == ["ARM", "AI"]
    assert len(parsed["segments"]) == 1
    assert parsed["summary"]["storyline"] == "Management walked through Q1."
    assert len(parsed["summary"]["key_points"]) == 1
    # The all_numbers list should have recovered the first two complete entries
    # plus possibly the truncated third as a best-effort.
    assert "$2.1B" in parsed["summary"]["all_numbers"]
    assert "42%" in parsed["summary"]["all_numbers"]


def test_parse_polish_response_strips_repetition_loop():
    """If Gemini spirals into a repetition loop in all_numbers, the repair
    step should collapse the loop."""
    looped = '{"language":"en","is_bilingual":false,"key_topics":[],"segments":[],' \
             '"summary":{"storyline":"","key_points":[],' \
             '"all_numbers":["$1","$2","50%","50%","50%","50%","50%","50%","50%","50%","50%","50%"],' \
             '"recent_updates":[],"financial_metrics":{"revenue":[],"profit":[],"orders":[]}}}'
    parsed = _parse_polish_response(looped)
    numbers = parsed["summary"]["all_numbers"]
    # Repetition of "50%" collapsed, but "$1" and "$2" preserved.
    assert "$1" in numbers
    assert "$2" in numbers
    fifty_count = sum(1 for n in numbers if n == "50%")
    assert fifty_count == 1, f"Expected exactly one '50%' after collapse, got {fifty_count}"


def test_parse_polish_response_skips_malformed_sub_points():
    """Resilience: non-dict sub_points entries are filtered out."""
    data = {
        "summary": {
            "storyline": "Test",
            "key_points": [
                {
                    "title": "Valid title",
                    "sub_points": [
                        {"text": "good sub", "supporting": "good support"},
                        "bogus string",
                        None,
                    ],
                }
            ],
        }
    }
    parsed = _parse_polish_response(json.dumps(data))
    kp = parsed["summary"]["key_points"][0]
    assert len(kp["sub_points"]) == 1
    assert kp["sub_points"][0]["text"] == "good sub"


# ---------------------------------------------------------------------------
# gemini_polish_text — text-input polish (URL ingest captions path)
# ---------------------------------------------------------------------------

def test_gemini_polish_text_returns_same_shape_as_audio_path():
    """gemini_polish_text produces a dict with the same keys that
    gemini_batch_transcribe returns, so the downstream pipeline doesn't care
    where the text came from. We mock the HTTP call so this runs offline."""
    from unittest.mock import patch
    from backend.app.services.live_transcription import gemini_polish_text

    # A canned Gemini response carrying a complete MeetingSummary.
    canned_response_json = {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "language": "en",
                "is_bilingual": False,
                "key_topics": ["test topic"],
                "segments": [{
                    "timestamp": "00:05",
                    "speaker": "",
                    "text_original": "Hello world.",
                    "text_english": "Hello world.",
                }],
                "summary": {
                    "storyline": "Short meeting.",
                    "key_points": [],
                    "all_numbers": [],
                    "recent_updates": [],
                    "financial_metrics": {"revenue": [], "profit": [], "orders": []},
                },
            })}]}
        }],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
    }

    class FakeResp:
        status_code = 200
        def json(self): return canned_response_json

    def fake_post(url, json=None, timeout=None):
        return FakeResp()

    input_segments = [
        {"timestamp": "00:05", "speaker": "", "text_original": "Hello world.", "text_english": ""},
    ]

    # Ensure API key is set so we don't trip the missing-key guard.
    import os
    os.environ["GEMINI_API_KEY"] = "test-key-for-unit-test"
    try:
        with patch("backend.app.services.live_transcription.requests.post", fake_post):
            result = gemini_polish_text(
                segments=input_segments,
                language_hint="en",
                note_id="test-note",
            )
    finally:
        # Don't leak the stub key to other tests.
        os.environ.pop("GEMINI_API_KEY", None)

    # Same keys as gemini_batch_transcribe returns.
    assert "language" in result
    assert "is_bilingual" in result
    assert "key_topics" in result
    assert "segments" in result
    assert "summary" in result
    assert "text" in result
    assert "input_tokens" in result
    assert "output_tokens" in result

    # Parsed values survived the round-trip.
    assert result["language"] == "en"
    assert result["key_topics"] == ["test topic"]
    assert len(result["segments"]) == 1
    assert result["summary"]["storyline"] == "Short meeting."


def test_gemini_polish_text_handles_no_api_key():
    """Degrades to empty shape when GEMINI_API_KEY is unset. We patch the
    os.environ lookup directly because load_dotenv() would otherwise rehydrate
    the real key from the dev .env file."""
    from unittest.mock import patch
    from backend.app.services.live_transcription import gemini_polish_text

    real_getenv = __import__("os").environ.get

    def fake_getenv(key, default=None):
        if key == "GEMINI_API_KEY":
            return None
        return real_getenv(key, default)

    with patch("backend.app.services.live_transcription.os.environ.get", side_effect=fake_getenv):
        result = gemini_polish_text(
            segments=[{"timestamp": "00:00", "speaker": "", "text_original": "x", "text_english": ""}],
            language_hint="en",
            note_id="test",
        )

    assert "error" in result
    assert result["summary"]["storyline"] == ""
    assert result["segments"] == []
