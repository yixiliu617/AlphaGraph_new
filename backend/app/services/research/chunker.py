"""
Paragraph- and sentence-aware chunker for press-release / CFO-commentary text.

Rules:
  - Target size: 200-400 "tokens" (approximated as 1.3 * words).
  - Primary split: paragraph boundaries (blank lines / double newlines).
  - Oversized paragraphs (> TARGET_MAX): split on sentence boundaries with
    abbreviation guards.
  - Undersized paragraphs (< TARGET_MIN): merged with adjacent paragraphs of
    the same kind.
  - Tables and bullet lists are preserved as single chunks regardless of size.
  - NEVER split mid-sentence.
  - Char offsets tracked on the ORIGINAL input so downstream consumers can
    jump to the exact location in the raw press release text.

No external dependencies — pure stdlib.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

TARGET_MIN_TOKENS = 80
TARGET_MAX_TOKENS = 400

# Approximate: 1 English token ~ 0.75 word. Use 1.3 * word_count as a tokens
# heuristic so we don't need tiktoken.
_WORDS_TO_TOKENS = 1.3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    text:        str
    char_start:  int
    char_end:    int
    token_count: int
    kind:        str = "paragraph"   # "paragraph" | "list" | "table" | "header"


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    words = len(text.split())
    return int(round(words * _WORDS_TO_TOKENS))


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

# Common abbreviations that shouldn't terminate a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr",
    "inc", "corp", "co", "ltd", "llc", "plc", "lp", "lllp",
    "vs", "etc", "e.g", "i.e", "u.s", "u.k", "u.n",
    "fig", "no", "vol", "jan", "feb", "mar", "apr", "jun",
    "jul", "aug", "sept", "sep", "oct", "nov", "dec",
    "sqq", "approx",
}


def _split_sentences(text: str) -> list[tuple[str, int, int]]:
    """
    Split text into (sentence, start_offset, end_offset) tuples relative to
    the input string. Conservative — prefers leaving borderline cases as one
    long sentence over splitting incorrectly mid-phrase.
    """
    results: list[tuple[str, int, int]] = []
    if not text:
        return results

    # Walk the string and emit sentences on . ! ? followed by whitespace + cap.
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch in ".!?":
            # Look ahead for whitespace + uppercase / digit to confirm boundary
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j >= n:
                # End of string — emit the final sentence and stop
                end = n
                sentence = text[start:end].strip()
                if sentence:
                    s_start = text.find(sentence, start)
                    results.append((sentence, s_start, s_start + len(sentence)))
                return results

            # Newline counts as a boundary too
            if text[j] == "\n" or text[j].isupper() or text[j].isdigit():
                # Check abbreviation: the word BEFORE the dot
                if ch == ".":
                    prev_start = i - 1
                    while prev_start >= start and text[prev_start] not in " \t\n":
                        prev_start -= 1
                    prev_word = text[prev_start + 1 : i].strip(".").lower()
                    if prev_word in _ABBREVIATIONS:
                        i = j
                        continue

                # Emit sentence
                end = i + 1
                sentence = text[start:end].strip()
                if sentence:
                    s_start = text.find(sentence, start)
                    if s_start < 0:
                        s_start = start
                    results.append((sentence, s_start, s_start + len(sentence)))
                start = j
                i = j
                continue
        i += 1

    # Trailing sentence (no terminal punctuation)
    if start < n:
        sentence = text[start:n].strip()
        if sentence:
            s_start = text.find(sentence, start)
            if s_start < 0:
                s_start = start
            results.append((sentence, s_start, s_start + len(sentence)))
    return results


# ---------------------------------------------------------------------------
# Paragraph segmentation
# ---------------------------------------------------------------------------

# Matches a blank-line or double-newline break. Our source text has been
# HTML-stripped via `re.sub(r"\s+", " ", ...)` so blank lines collapsed to
# single spaces — we therefore fall back to a heuristic split on long runs
# of whitespace + sentence-end when there are no newlines at all.

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def _segment_paragraphs(text: str) -> list[tuple[str, int, int, str]]:
    """
    Return a list of (paragraph_text, start, end, kind) tuples.

    kind is one of:
      "paragraph" — normal body
      "list"      — bullet/number list (kept intact)
      "header"    — short uppercase-ish heading
      "table"     — fallback when we can't tell
    """
    paragraphs: list[tuple[str, int, int, str]] = []
    if not text:
        return paragraphs

    # Our upstream HTML stripper normalizes whitespace, so real paragraph
    # breaks from the source HTML are usually gone. Fall back: synthesize
    # paragraph breaks on "... . " followed by a capitalized sentence after
    # a lot of sentences, OR treat "### " / bullet markers as breaks.
    if "\n\n" in text:
        raw_paragraphs = _PARA_SPLIT_RE.split(text)
        offset = 0
        for rp in raw_paragraphs:
            stripped = rp.strip()
            if not stripped:
                offset += len(rp) + 2
                continue
            s = text.find(stripped, offset)
            if s < 0:
                s = offset
            e = s + len(stripped)
            kind = _classify_paragraph(stripped)
            paragraphs.append((stripped, s, e, kind))
            offset = e
        return paragraphs

    # No real paragraph breaks — synthesize from sentence runs. Group every
    # 4-6 sentences into a paragraph.
    sentences = _split_sentences(text)
    if not sentences:
        return [(text.strip(), 0, len(text), "paragraph")]

    group_size = 5
    for i in range(0, len(sentences), group_size):
        group = sentences[i : i + group_size]
        start = group[0][1]
        end = group[-1][2]
        ptext = text[start:end].strip()
        if ptext:
            paragraphs.append((ptext, start, end, "paragraph"))
    return paragraphs


_HEADER_RE = re.compile(r"^[A-Z][A-Z0-9 \-&,.:/]{4,80}$")


def _classify_paragraph(text: str) -> str:
    # Bullet list heuristic: starts with "• " / "- " / "* " or has multiple
    # embedded bullet characters.
    if text.count("•") >= 2 or text.startswith(("•", "- ", "* ")):
        return "list"
    # Header heuristic: short, mostly uppercase letters
    if len(text) < 100 and _HEADER_RE.match(text):
        return "header"
    return "paragraph"


# ---------------------------------------------------------------------------
# Main chunker
# ---------------------------------------------------------------------------

def chunk_document(text: str) -> list[Chunk]:
    """
    Split a document into paragraph-aware chunks targeting 200-400 tokens.
    Char offsets are relative to the input text.
    """
    if not text or not text.strip():
        return []

    paragraphs = _segment_paragraphs(text)
    chunks: list[Chunk] = []

    buffer: list[tuple[str, int, int, str]] = []
    buffer_tokens = 0

    def flush_buffer():
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        texts = [p[0] for p in buffer]
        s = buffer[0][1]
        e = buffer[-1][2]
        merged_text = "\n\n".join(texts).strip()
        tok = _count_tokens(merged_text)
        kind = buffer[0][3] if len(buffer) == 1 else "paragraph"
        chunks.append(Chunk(text=merged_text, char_start=s, char_end=e,
                            token_count=tok, kind=kind))
        buffer = []
        buffer_tokens = 0

    for ptext, s, e, kind in paragraphs:
        tok = _count_tokens(ptext)

        # Oversized single paragraph -> split on sentences, but flush buffer first
        if tok > TARGET_MAX_TOKENS and kind not in ("list", "table"):
            flush_buffer()
            chunks.extend(_split_oversized_paragraph(ptext, s))
            continue

        # Lists and tables are always single chunks even if large.
        if kind in ("list", "table"):
            flush_buffer()
            chunks.append(Chunk(text=ptext, char_start=s, char_end=e,
                                token_count=tok, kind=kind))
            continue

        # Merge small paragraphs into the buffer until it exceeds the target.
        if buffer_tokens + tok > TARGET_MAX_TOKENS and buffer_tokens >= TARGET_MIN_TOKENS:
            flush_buffer()

        buffer.append((ptext, s, e, kind))
        buffer_tokens += tok

        if buffer_tokens >= TARGET_MIN_TOKENS and buffer_tokens <= TARGET_MAX_TOKENS:
            # "Good enough" — if the next paragraph would overflow, flush in
            # the next iteration via the overflow check.
            pass

    flush_buffer()
    return chunks


def _split_oversized_paragraph(text: str, base_offset: int) -> list[Chunk]:
    """
    Split a single oversized paragraph by sentence boundaries. Accumulates
    sentences until reaching TARGET_MAX_TOKENS, then flushes.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return [Chunk(text=text, char_start=base_offset,
                      char_end=base_offset + len(text),
                      token_count=_count_tokens(text), kind="paragraph")]

    out: list[Chunk] = []
    buf: list[tuple[str, int, int]] = []
    buf_tokens = 0

    def flush():
        nonlocal buf, buf_tokens
        if not buf:
            return
        merged = " ".join(s[0] for s in buf).strip()
        s_start = base_offset + buf[0][1]
        s_end   = base_offset + buf[-1][2]
        out.append(Chunk(text=merged, char_start=s_start, char_end=s_end,
                          token_count=_count_tokens(merged), kind="paragraph"))
        buf = []
        buf_tokens = 0

    for sent_text, sent_start, sent_end in sentences:
        tok = _count_tokens(sent_text)
        if buf_tokens + tok > TARGET_MAX_TOKENS and buf_tokens >= TARGET_MIN_TOKENS:
            flush()
        buf.append((sent_text, sent_start, sent_end))
        buf_tokens += tok

    flush()
    return out
