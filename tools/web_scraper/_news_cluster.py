"""Shared helpers for news-article clustering.

Used by both the live scraper (news_tracker.py) and the one-off
re-clustering migration (recluster_news.py) so their heuristics stay
in lockstep.

Matching strategy is layered:

1. High-precision fuzzy match — SequenceMatcher ratio > 0.7 on the
   normalised title. Catches near-duplicates from syndication / minor
   rewrites.

2. Anchor-token fallback for stories that share a distinctive entity
   but use very different framing across outlets (the GPT-5.5
   headlines are the canonical case). Two titles cluster when they
   share either:
     - >= 1 "digit anchor" (token with both digits AND letters, like
       "gpt55", "h100", "5nm", "q1" — these are product/version/spec
       tokens, very rare, very distinctive), or
     - >= 2 "alpha anchors" (pure-alpha tokens length >= 5 that are
       not common words — entity names like "openai", "anthropic",
       "nvidia", "tsmc" length 4 is excluded to avoid false positives
       on 4-letter stopwords).

   The alpha path is weaker so it requires two overlaps. Both paths
   also require the two articles to be within a 48 h window, to
   avoid collapsing unrelated stories months apart that happen to
   mention the same technology.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

_STOPWORDS = frozenset({
    "about", "after", "again", "among", "being", "could", "every",
    "first", "going", "having", "later", "never", "other", "since",
    "still", "their", "there", "these", "those", "under", "until",
    "which", "while", "would", "should", "quarter", "report",
    "shares", "stock", "market", "company", "business", "earnings",
    "revenue", "release", "releases", "update", "updates", "service",
    "services", "across", "against", "between", "through", "before",
})

ANCHOR_WINDOW_HOURS = 48


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def cluster_id(norm: str) -> str:
    return hashlib.blake2b(norm.encode("utf-8"), digest_size=6).hexdigest()


def extract_anchors(norm: str) -> tuple[frozenset[str], frozenset[str]]:
    """Split a normalised title into (digit_anchors, alpha_anchors).

    - digit anchor: token with >= 1 digit AND >= 1 letter, len >= 2.
      Pure-digit tokens like "2026" or "100" are excluded — too noisy.
    - alpha anchor: pure-alpha token len >= 5, not in the stopword set.

    Coalesce adjacent alpha + pure-digit tokens into a compound digit
    anchor so that "gpt 5 5" (from "GPT 5.5") and "gpt55" (from
    "GPT-5.5") both produce the anchor "gpt55" and can match each
    other. Without this, outlets that write version numbers with a
    space won't cluster with outlets that write them with a hyphen.
    """
    tokens = [t for t in norm.split() if t]
    digit: set[str] = set()
    alpha: set[str] = set()

    for tok in tokens:
        has_d = any(c.isdigit() for c in tok)
        has_a = any(c.isalpha() for c in tok)
        if has_d and has_a and len(tok) >= 2:
            digit.add(tok)
        elif has_a and not has_d and len(tok) >= 5 and tok not in _STOPWORDS:
            alpha.add(tok)

    # Second pass: merge "<alpha-run> <digit-run>+" sequences into one
    # compound digit anchor so "gpt 5 5" (space-delimited from "GPT 5.5")
    # produces the same "gpt55" anchor as "GPT-5.5" does. Require alpha
    # length >= 3 to avoid absorbing short prepositions ("to", "in", "at",
    # "of") into garbage anchors like "to10000".
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.isalpha() and len(t) >= 3 and i + 1 < n and tokens[i + 1].isdigit():
            merged = t
            j = i + 1
            while j < n and tokens[j].isdigit():
                merged += tokens[j]
                j += 1
            digit.add(merged)
            i = j
        else:
            i += 1

    return frozenset(digit), frozenset(alpha)


def anchors_match(
    a_digit: frozenset[str],
    a_alpha: frozenset[str],
    b_digit: frozenset[str],
    b_alpha: frozenset[str],
) -> bool:
    if a_digit & b_digit:
        return True
    if len(a_alpha & b_alpha) >= 2:
        return True
    return False


def _parse_iso(s) -> datetime | None:
    if not s:
        return None
    try:
        s = str(s)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def within_hours(a_iso, b_iso, hours: float = ANCHOR_WINDOW_HOURS) -> bool:
    """True if two ISO-8601 pub timestamps are within `hours`."""
    a = _parse_iso(a_iso)
    b = _parse_iso(b_iso)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= hours * 3600
