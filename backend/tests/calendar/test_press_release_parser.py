from pathlib import Path

import pytest

from backend.app.services.calendar.enrichment.press_release_parser import (
    parse_press_release,
)

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_extracts_webcast_dial_in_pin_from_nvda_style():
    text = _read("pr_nvda_q4fy2026.txt")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.nvidia.com/events/event-details/q4-2026"
    # Phone normalized: any digits/separators acceptable, just must be the domestic one
    assert "800" in out["dial_in_phone"] and "555-0123" in out["dial_in_phone"]
    assert out["dial_in_pin"] == "8675309"


def test_handles_release_with_no_dial_in():
    text = _read("pr_no_dial_in.txt")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.acme.com/q1-2026"
    assert out["dial_in_phone"] is None
    assert out["dial_in_pin"] is None


def test_handles_international_phone_format():
    text = _read("pr_intl_phone.txt")
    out = parse_press_release(text)
    assert out["webcast_url"].startswith("https://www.tsmc.com")
    assert "886" in out["dial_in_phone"]
    assert out["dial_in_pin"] == "1234567"


def test_returns_all_none_for_unrelated_text():
    out = parse_press_release("This is a press release about a new product launch.")
    assert out == {
        "webcast_url": None, "dial_in_phone": None, "dial_in_pin": None,
        "press_release_url": None,
    }


def test_strips_trailing_punctuation_from_url():
    text = ("The webcast can be accessed at "
            "https://investor.example.com/q1, including replay.")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.example.com/q1"
