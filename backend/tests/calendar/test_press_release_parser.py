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
    assert out["webcast_url"] == "https://www.tsmc.com/english/aboutTSMC/ir"
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


def test_extracts_from_live_audio_stream_keyword():
    text = _read("pr_audio_stream.txt")
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.sample.com/q1-2026/audio"
    assert "877" in out["dial_in_phone"] and "555-2200" in out["dial_in_phone"]
    assert out["dial_in_pin"] == "9988776"


def test_extracts_webcast_url_beyond_80_chars():
    """Real NVDA-style: webcast keyword far from the URL via investor-relations phrasing."""
    text = (
        "A live webcast (listen-only mode) of the conference call will be "
        "accessible at NVIDIA's investor relations website, "
        "http://investor.nvidia.com/events/q4-2026."
    )
    out = parse_press_release(text)
    assert out["webcast_url"] == "http://investor.nvidia.com/events/q4-2026"


def test_extracts_bare_host_url_and_prepends_https():
    """Real Apple PRs use 'www.apple.com/investor/earnings-call/...' without
    the http(s):// scheme. Parser should prepend https://."""
    text = (
        "Apple will host a webcast at "
        "www.apple.com/investor/earnings-call/quarterly-earnings-q1-2026/ "
        "today at 5:00 p.m. ET."
    )
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://www.apple.com/investor/earnings-call/quarterly-earnings-q1-2026/"


def test_extracts_bare_investor_subdomain_url():
    """Bare investor.* hostnames should also get the https:// prefix."""
    text = (
        "Listen to the call at investor.acme.com/events/q3-2026 starting "
        "at 3:00 p.m. ET."
    )
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.acme.com/events/q3-2026"


def test_does_not_prepend_https_to_full_url():
    """Existing https:// URLs should not be doubly-prefixed."""
    text = "Webcast at https://investor.example.com/q4 today."
    out = parse_press_release(text)
    assert out["webcast_url"] == "https://investor.example.com/q4"


def test_does_not_match_bare_host_without_path():
    """A bare hostname mention without a path (e.g. 'visit www.apple.com')
    should not be claimed as a webcast URL because it lacks specificity."""
    text = "For more information, visit www.apple.com today."
    out = parse_press_release(text)
    # The new pattern requires a / in the bare-host alternative, so this
    # should NOT match -- the URL is too generic.
    assert out["webcast_url"] is None
