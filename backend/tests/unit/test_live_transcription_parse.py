"""
Unit tests for the structured-output parser in live_transcription.
We don't hit the Gemini API; we only verify our handling of the JSON shape.
"""

import json
import pytest

from backend.app.services.live_transcription import (
    _parse_polish_response,
    _flatten_segments_to_markdown,
    _extract_gemini_text,
)


# ---------------------------------------------------------------------------
# _extract_gemini_text -- guards against `KeyError: 'content'` in the wild
# ---------------------------------------------------------------------------

def test_extract_text_happy_path():
    result = {
        "candidates": [
            {"content": {"parts": [{"text": "hello world"}]},
             "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
    }
    text, err = _extract_gemini_text(result)
    assert text == "hello world"
    assert err is None


def test_extract_text_concatenates_multiple_parts():
    result = {
        "candidates": [
            {"content": {"parts": [{"text": "foo "}, {"text": "bar"}]},
             "finishReason": "STOP"}
        ],
    }
    text, err = _extract_gemini_text(result)
    assert text == "foo bar"
    assert err is None


def test_extract_text_safety_block_no_content():
    """Gemini SAFETY filter: candidate has finishReason but no content key."""
    result = {
        "candidates": [
            {"finishReason": "SAFETY", "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS", "probability": "HIGH"}]}
        ],
    }
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "SAFETY" in err
    assert "no content" in err.lower()


def test_extract_text_recitation_block_no_content():
    """Gemini RECITATION filter: same shape as SAFETY -- finishReason without content."""
    result = {
        "candidates": [{"finishReason": "RECITATION"}],
    }
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "RECITATION" in err


def test_extract_text_prompt_block():
    """Prompt-level block: top-level promptFeedback.blockReason, no candidates at all."""
    result = {
        "promptFeedback": {"blockReason": "OTHER"},
    }
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "OTHER" in err


def test_extract_text_no_candidates():
    """Empty response shape: no candidates and no promptFeedback."""
    result = {"usageMetadata": {"promptTokenCount": 100}}
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "no candidates" in err.lower()


def test_extract_text_empty_parts():
    """content present but parts is an empty list (rare; usually MAX_TOKENS at budget=0)."""
    result = {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "MAX_TOKENS" in err


def test_extract_text_whitespace_only():
    """If text is just whitespace it's effectively empty."""
    result = {"candidates": [{"content": {"parts": [{"text": "   \n"}]}, "finishReason": "STOP"}]}
    text, err = _extract_gemini_text(result)
    assert text == ""
    assert err is not None
    assert "empty" in err.lower()


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
        "all_numbers": [
            {"label": "Q1 revenue", "value": "$2.1B", "quote": "Our Q1 revenue was $2.1B."},
            {"label": "YoY growth rate", "value": "20%", "quote": "Revenue grew 20% year-over-year."},
            {"label": "gross margin", "value": "42%", "quote": "Gross margin came in at 42%, up 200bps."},
        ],
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
    # all_numbers now carries {label, value, quote} entries.
    assert len(summary["all_numbers"]) == 3
    values = [n["value"] for n in summary["all_numbers"]]
    assert "$2.1B" in values
    assert "20%" in values
    assert "42%" in values
    # Label + quote preserved.
    first = next(n for n in summary["all_numbers"] if n["value"] == "$2.1B")
    assert first["label"] == "Q1 revenue"
    assert "Our Q1 revenue was $2.1B" in first["quote"]
    assert "Closed the X acquisition last month" in summary["recent_updates"]
    assert summary["financial_metrics"]["revenue"] == ["Q1 revenue $2.1B, up 20% YoY"]
    assert summary["financial_metrics"]["profit"] == ["Operating margin 28%, up 200bps YoY"]
    assert summary["financial_metrics"]["orders"] == ["Backlog $8.5B, up 30% QoQ"]


def test_parse_summary_coerces_legacy_string_numbers():
    """Notes written before the NumberMention refactor had all_numbers as a
    list of plain strings. The parser must still handle them by promoting each
    string into a {label: '', value: str, quote: ''} object so rendering
    doesn't crash on existing data."""
    legacy = {"summary": {"all_numbers": ["$2.1B", "20% YoY", "42%"]}}
    parsed = _parse_polish_response(json.dumps(legacy))
    nums = parsed["summary"]["all_numbers"]
    assert len(nums) == 3
    assert all(set(n.keys()) == {"label", "value", "quote"} for n in nums)
    assert nums[0] == {"label": "", "value": "$2.1B", "quote": ""}
    assert nums[1]["value"] == "20% YoY"
    assert nums[2]["value"] == "42%"


def test_parse_summary_dedupes_number_mentions_by_label_and_value():
    """Repetition spirals should collapse — but only when both value AND label
    match. Two mentions of '50%' with different labels (e.g. 'gross margin' vs
    'market share') should both be kept."""
    data = {"summary": {"all_numbers": [
        {"label": "Q1 revenue", "value": "$2.1B", "quote": "q1"},
        {"label": "Q1 revenue", "value": "$2.1B", "quote": "q1 dup"},  # dup — dropped
        {"label": "Q1 revenue", "value": "$2.1B", "quote": "q1 dup2"}, # dup — dropped
        {"label": "gross margin", "value": "50%", "quote": "gm"},
        {"label": "market share", "value": "50%", "quote": "ms"},      # same value, different label — kept
    ]}}
    parsed = _parse_polish_response(json.dumps(data))
    nums = parsed["summary"]["all_numbers"]
    assert len(nums) == 3, f"Expected 3 after dedupe, got {len(nums)}"
    labels = sorted(n["label"] for n in nums)
    assert labels == ["Q1 revenue", "gross margin", "market share"]


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
        "all_numbers": [
          {"label": "Q1 revenue", "value": "$2.1B", "quote": "Q1 revenue was $2.1B."},
          {"label": "gross margin", "value": "42%", "quote": "Margin hit 42%."},
          {"label": "cash position", "value": "17 tri"""
    parsed = _parse_polish_response(truncated)
    # Repair should recover the well-formed parts even though the last entry was cut off.
    assert parsed["language"] == "en"
    assert parsed["key_topics"] == ["ARM", "AI"]
    assert len(parsed["segments"]) == 1
    assert parsed["summary"]["storyline"] == "Management walked through Q1."
    assert len(parsed["summary"]["key_points"]) == 1
    values = [n["value"] for n in parsed["summary"]["all_numbers"]]
    assert "$2.1B" in values
    assert "42%" in values


def test_parse_polish_response_strips_repetition_loop_on_legacy_string_numbers():
    """Legacy notes (before NumberMention refactor) may still carry string-shaped
    all_numbers with repetition loops. Dedupe should collapse them."""
    looped = '{"language":"en","is_bilingual":false,"key_topics":[],"segments":[],' \
             '"summary":{"storyline":"","key_points":[],' \
             '"all_numbers":["$1","$2","50%","50%","50%","50%","50%","50%","50%","50%","50%","50%"],' \
             '"recent_updates":[],"financial_metrics":{"revenue":[],"profit":[],"orders":[]}}}'
    parsed = _parse_polish_response(looped)
    numbers = parsed["summary"]["all_numbers"]
    # After coercion + dedupe: one "$1", one "$2", one "50%".
    assert len(numbers) == 3
    values = [n["value"] for n in numbers]
    assert "$1" in values
    assert "$2" in values
    assert "50%" in values


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
# gemini_generate_summary — text-only summary generation (stage 2 of the split
# transcribe→summarise pipeline). Re-runnable without re-paying audio cost.
# ---------------------------------------------------------------------------

def test_gemini_generate_summary_returns_summary_only():
    """gemini_generate_summary returns ONLY the summary + token usage. It no
    longer produces transcript fields — those come from gemini_batch_transcribe
    in stage 1 of the pipeline."""
    from unittest.mock import patch
    from backend.app.services.live_transcription import gemini_generate_summary

    canned_response_json = {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({
                "storyline": "A short test meeting.",
                "key_points": [
                    {"title": "Revenue growth", "sub_points": [
                        {"text": "Grew 20%", "supporting": "Driven by ARM."}
                    ]}
                ],
                "all_numbers": [
                    {"label": "Q1 revenue", "value": "$2.1B", "quote": "Our Q1 revenue was $2.1B."},
                    {"label": "growth rate", "value": "20%", "quote": "Revenue grew 20% year-over-year."},
                ],
                "recent_updates": ["Closed X acquisition"],
                "financial_metrics": {
                    "revenue": ["Q1 revenue $2.1B"],
                    "profit": [],
                    "orders": [],
                },
            })}]}
        }],
        "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 300},
    }

    class FakeResp:
        status_code = 200
        def json(self): return canned_response_json

    def fake_post(url, json=None, timeout=None):
        return FakeResp()

    import os
    os.environ["GEMINI_API_KEY"] = "test-key-for-unit-test"
    try:
        with patch("backend.app.services.live_transcription.requests.post", fake_post):
            result = gemini_generate_summary(
                segments=[
                    {"timestamp": "00:05", "speaker": "", "text_original": "Hello.", "text_english": "Hello."},
                ],
                language_hint="en",
                note_id="test-note",
            )
    finally:
        os.environ.pop("GEMINI_API_KEY", None)

    # Shape: summary + token counts only. No transcript fields.
    assert set(result.keys()) >= {"summary", "input_tokens", "output_tokens"}
    assert "segments" not in result   # not this function's job
    assert "language" not in result   # also not this function's job

    summary = result["summary"]
    assert summary["storyline"] == "A short test meeting."
    assert len(summary["key_points"]) == 1
    assert len(summary["all_numbers"]) == 2
    first = summary["all_numbers"][0]
    assert first["label"] == "Q1 revenue"
    assert first["value"] == "$2.1B"
    assert "$2.1B" in first["quote"]


def test_gemini_generate_summary_handles_no_api_key():
    """Degrades to empty summary shape when GEMINI_API_KEY is unset. We patch
    the os.environ lookup directly because load_dotenv() would otherwise
    rehydrate the real key from the dev .env file."""
    from unittest.mock import patch
    from backend.app.services.live_transcription import gemini_generate_summary

    real_getenv = __import__("os").environ.get

    def fake_getenv(key, default=None):
        if key == "GEMINI_API_KEY":
            return None
        return real_getenv(key, default)

    with patch("backend.app.services.live_transcription.os.environ.get", side_effect=fake_getenv):
        result = gemini_generate_summary(
            segments=[{"timestamp": "00:00", "speaker": "", "text_original": "x", "text_english": ""}],
            language_hint="en",
            note_id="test",
        )

    assert "error" in result
    assert result["summary"]["storyline"] == ""
    assert result["summary"]["all_numbers"] == []


def test_gemini_generate_summary_handles_empty_segments():
    """No transcript content means nothing to summarise. Return empty without
    burning a token."""
    from backend.app.services.live_transcription import gemini_generate_summary

    import os
    os.environ["GEMINI_API_KEY"] = "test-key-for-unit-test"
    try:
        result = gemini_generate_summary(
            segments=[{"timestamp": "", "speaker": "", "text_original": "", "text_english": ""}],
            language_hint="en",
            note_id="test",
        )
    finally:
        os.environ.pop("GEMINI_API_KEY", None)

    assert "error" in result
    assert "No transcript content" in result["error"]
    assert result["summary"]["storyline"] == ""
