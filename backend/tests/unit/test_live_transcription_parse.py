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
