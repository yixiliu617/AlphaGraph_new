"""Method A: extract calendar soft fields from 8-K Item 2.02 press release text.

The text comes from earnings_releases/ticker=*.parquet, column text_raw.
Each press release has a "Conference Call Information" section near the
bottom with the webcast URL, dial-in phone number, and conference ID.

Regex-only -- no LLM. Free, deterministic, fast. ~95% recall on past events.

ASCII-only print/log per CLAUDE.md.
"""
from __future__ import annotations

import re
from typing import TypedDict


class SoftFields(TypedDict):
    webcast_url:        str | None
    dial_in_phone:      str | None
    dial_in_pin:        str | None
    press_release_url:  str | None


# First match wins. Patterns target the standard "conference call" disclosure
# paragraph nearly every US-listed company includes near the bottom of the
# release. Case-insensitive (re.IGNORECASE) and DOTALL (.{0,80}? spans
# arbitrary line breaks between the keyword and the URL).
#
# URL terminator class excludes whitespace, common URL-adjacent punctuation,
# closing brackets ()}>]), quotes ("'), comma, and Unicode curly quotes
# (U+2018-U+201D) which sometimes wrap URLs in copy-pasted text. Curly
# quotes are written as \u escapes so the source file stays pure ASCII.
_WEBCAST_RX = re.compile(
    "(?:webcast|live\\s+(?:audio\\s+)?stream|listen(?:\\s+to\\s+the\\s+call)?)"
    ".{0,80}?"
    "(https?://[^\\s)}<\\]\"'>,\u2018\u2019\u201c\u201d]+)",
    re.IGNORECASE | re.DOTALL,
)
# Phone separator class: digits, spaces, hyphens, parens, AND periods.
# US PRs frequently use 800.555.0123 style; Asian PRs use 886-2-1234.
_PHONE_RX = re.compile(
    r"(?:dial[-\s]?in|domestic|toll[-\s]?free|conference\s+(?:call\s+)?number)"
    r"[^\d+]{0,30}"
    r"(\+?[\d][\d\s\-\(\).]{8,18}\d)",
    re.IGNORECASE,
)
# Conference IDs from Cisco WebEx, Zoom, ChorusCall, Q4 Inc are almost
# universally 6-10 digits. 4-5-digit codes are vanishingly rare in real
# IR releases and mostly false positives (years like "2026", small numbers
# in surrounding text). Require >=6 digits AND keep the digit run on the
# same line as the keyword (no newline in the gap).
_PIN_RX = re.compile(
    r"(?:conference\s+id|access\s+code|passcode|pin\s+number)"
    r"[^\d\n]{0,15}"
    r"(\d{6,12})",
    re.IGNORECASE,
)
# Trailing characters we want to strip if they leak into a URL match.
_TRAILING_TRASH = ".,;:!?)"


def parse_press_release(text: str) -> SoftFields:
    """Extract soft fields from a press-release body. Missing fields -> None.

    Does NOT call validate_url -- caller is responsible for validation.
    """
    return {
        "webcast_url":       _extract_url(text),
        "dial_in_phone":     _extract_phone(text),
        "dial_in_pin":       _extract_pin(text),
        # press_release_url: not extractable from the press release body
        # itself; the SEC filing URL is already stored as filing_url.
        "press_release_url": None,
    }


def _extract_url(text: str) -> str | None:
    m = _WEBCAST_RX.search(text)
    if not m:
        return None
    url = m.group(1)
    url = url.rstrip(_TRAILING_TRASH)
    return url or None


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RX.search(text)
    if not m:
        return None
    phone = m.group(1).strip()
    return phone or None


def _extract_pin(text: str) -> str | None:
    m = _PIN_RX.search(text)
    return m.group(1) if m else None
