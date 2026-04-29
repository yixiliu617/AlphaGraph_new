"""Method A: extract calendar soft fields from 8-K Item 2.02 press release text.

The text comes from earnings_releases/ticker=*.parquet, column text_raw.
Each press release has a "Conference Call Information" section near the
bottom with the webcast URL, dial-in phone number, and conference ID.

Strategy: find the conference-call section anchor, then extract URLs /
phones / PINs from the next ~800 chars. Falls back to whole-text scan if
no anchor is found, or if the section window has no match. Regex-only --
no LLM. Free, deterministic, fast.

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


# ---------------------------------------------------------------------------
# Section anchor: find the start of the conference-call disclosure paragraph
# ---------------------------------------------------------------------------

# Wide list of phrases that mark the start of the disclosure paragraph.
_SECTION_ANCHOR_RX = re.compile(
    r"(?:conference\s+call|earnings\s+call|webcast|live\s+(?:audio|broadcast)|"
    r"listen[-\s]?(?:only|live|to\s+the\s+call)|replay\s+of\s+the\s+call|"
    r"dial[-\s]?in|please\s+dial|to\s+access\s+the\s+call)",
    re.IGNORECASE,
)

# After the anchor we look forward this many characters for URLs / phones / PINs.
_SECTION_WINDOW_CHARS = 800

# ---------------------------------------------------------------------------
# Field patterns (applied within the disclosure window)
# ---------------------------------------------------------------------------

# URLs: a full http(s):// URL OR a bare hostname starting with www., investor.,
# or ir. that the press-release author forgot to scheme. Real browsers prepend
# https://; our validator (Python requests) raises MissingSchema without it.
# The bare-host alternative requires at least one dot AND a slash to keep it
# specific (rejects e.g. "see Q4." or "visit www.apple.com" with no path).
_URL_ANY_RX = re.compile(
    r"(?:https?://[^\s)}<\]\"'>,;]+|"
    r"(?<![\w.])(?:www\.|investor\.|ir\.)[A-Za-z0-9\-]+\.[A-Za-z]{2,}/[^\s)}<\]\"'>,;]*)",
    re.IGNORECASE,
)
# IR-shaped URL hint: prefer URLs whose path/host suggests investor relations
# / earnings / webcast / events / quarterly results / a Q\d period token.
_IR_HINT_RX = re.compile(
    r"(?i)(?:investor|ir\.|earnings|webcast|events|q\d|results|listen|broadcast)",
)
# Trailing punctuation that often leaks into the URL match.
_TRAILING_TRASH = ".,;:!?)\"'"

# Phones: 10-15 digit run with separators (space, hyphen, period, paren).
# We capture conservatively to keep the body length to the typical phone shape;
# leading optional + for international.
_PHONE_RX = re.compile(
    r"(\+?\d[\d\s().\-]{8,18}\d)",
    re.IGNORECASE,
)
# Phones we accept must contain at least one digit BEFORE a separator -- this
# eliminates spurious matches like "1, 2, 3" in numbered lists. The {8,18}
# body length already enforces a minimum total of ~10 chars.
# Phones we reject: those that are all-zero or all-same digit (000-000-0000).
_BAD_PHONE_RX = re.compile(r"^[+\s().\-]*0+[\s().\-0]*$")  # all zeros
# Conservative phone keyword anchors -- we only accept phones found within
# 80 chars of one of these. (Avoids matching e.g. SEC-filing reference numbers
# that aren't dial-ins.)
_PHONE_KEYWORD_RX = re.compile(
    r"(?:dial[-\s]?in|please\s+dial|toll[-\s]?free|domestic|"
    r"call[-\s]?in|conference\s+(?:call\s+)?number|"
    r"(?:u\.?s\.?\s*(?:and|/)?\s*canada|north\s+america|international|outside)"
    r"(?:\s+(?:dial[-\s]?in|callers))?)",
    re.IGNORECASE,
)

# PINs: 6-12 digit run after a recognized PIN keyword (same line).
# Word boundary on "pin" prevents matching it inside English words like
# "sweeping" or "happen". Other keywords are multi-word so no boundary needed.
_PIN_RX = re.compile(
    r"(?:conference\s+id|access\s+code|pass[\s-]?code|\bpin(?:\s+number)?\b|"
    r"confirmation\s+(?:number|code)|meeting\s+id|reference\s+(?:number|code))"
    r"\s*[:#]?[^\d\n]{0,15}(\d{6,12})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_press_release(text: str) -> SoftFields:
    """Extract soft fields from a press-release body. Missing fields -> None.

    For each field: try the section window first (after a conference-call
    anchor), then fall back to whole-text scan if the window had no match.
    """
    section = _section_window(text)
    return {
        "webcast_url":       _extract_url(text, section),
        "dial_in_phone":     _extract_phone(text, section),
        "dial_in_pin":       _extract_pin(text, section),
        # Already stored on each event row as filing_url -- we don't extract here.
        "press_release_url": None,
    }


def _section_window(text: str) -> str | None:
    """Return the slice of `text` from the first conference-call anchor
    forward by _SECTION_WINDOW_CHARS. None if no anchor is present (caller
    falls back to whole-text scan)."""
    m = _SECTION_ANCHOR_RX.search(text)
    if not m:
        return None
    start = m.start()
    return text[start : start + _SECTION_WINDOW_CHARS]


def _extract_url(text: str, section: str | None) -> str | None:
    """Extract a webcast URL.

    Try the section window first (URL nearest the conference-call anchor).
    If empty, scan the whole text and prefer the first URL whose host/path
    matches IR-shaped keywords (investor, earnings, webcast, events, Q4, ...).
    """
    if section:
        m = _URL_ANY_RX.search(section)
        if m:
            return _clean_url(m.group(0))
    # Fallback: whole text, prefer IR-shaped URLs.
    for m in _URL_ANY_RX.finditer(text):
        url = m.group(0)
        if _IR_HINT_RX.search(url):
            return _clean_url(url)
    # Last resort: any URL at all.
    m = _URL_ANY_RX.search(text)
    return _clean_url(m.group(0)) if m else None


def _clean_url(url: str) -> str | None:
    """Strip trailing punctuation and prepend https:// when the parser
    captured a bare host (e.g. "www.apple.com/investor/..."). Press
    releases routinely omit the scheme; real browsers prepend it but
    Python requests raises MissingSchema without it."""
    url = url.rstrip(_TRAILING_TRASH)
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        # Only prepend if the captured token actually has a host with a
        # dot before the first slash. The regex enforces this already,
        # but defense in depth is cheap.
        first_slash = url.find("/")
        host = url if first_slash == -1 else url[:first_slash]
        if "." not in host:
            return None
        url = "https://" + url
    return url


def _extract_phone(text: str, section: str | None) -> str | None:
    """Return the first plausible phone number near a phone-keyword anchor.

    Walks each keyword match and looks for a phone within 80 chars after.
    This double-anchor approach prevents matching bare digit runs that
    happen to be near other content (SEC filing IDs, ticker symbols, etc.).

    Tries the section window first, then falls back to whole-text scan.
    """
    for haystack in (section, text):
        if haystack is None:
            continue
        for kw in _PHONE_KEYWORD_RX.finditer(haystack):
            # Look in the next 80 chars after the keyword.
            local = haystack[kw.end() : kw.end() + 80]
            m = _PHONE_RX.search(local)
            if not m:
                continue
            phone = m.group(1).strip().rstrip(_TRAILING_TRASH)
            # Reject all-zero or pathological matches.
            if _BAD_PHONE_RX.match(phone):
                continue
            # Require at least 10 digits in the phone (reject "1234567890"
            # lookalikes that are actually too short after stripping separators).
            digit_count = sum(c.isdigit() for c in phone)
            if digit_count < 10:
                continue
            return phone
        # Don't double-search if section IS text.
        if haystack is text:
            break
    return None


def _extract_pin(text: str, section: str | None) -> str | None:
    """Return the first conference PIN (6-12 digits after a PIN keyword,
    same line). Tries section window first, then whole text."""
    if section:
        m = _PIN_RX.search(section)
        if m:
            return m.group(1)
    m = _PIN_RX.search(text)
    return m.group(1) if m else None
