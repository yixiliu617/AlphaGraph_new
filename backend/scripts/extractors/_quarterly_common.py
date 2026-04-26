"""
Shared primitives for company-specific quarterly-report extractors.

Each ticker has its own `{ticker}_management_report.py` (or similar) with
the company's idiosyncratic ROW_SPECS, section anchors, period-label
shape, and any layout-specific quirks. The parts that were truly shared
across TSMC's three eras and turned out to also be shared by UMC's
Word-era reports are factored into this module:

  - Number tokenisation that handles BOTH "one number per line" AND
    "multiple numbers on one line" (the Workiva NT$-billion quirk).
  - Label-continuation handling (multi-line row labels like
    "Net Income Attributable to Shareholders of the / Parent Company").
  - Section slicing (anchor-bounded slices of `lines`).
  - Period header detection (e.g. "1Q26 / 4Q25 / 1Q25").
  - The `_parse_value_table` driver that walks `(label_regex, metric, unit)`
    specs against a section.

A new extractor only needs to define:
  - The list of ROW_SPECS per sub-table.
  - Section anchors (and end markers).
  - The company-specific periodic-format regex if their period labels
    differ from `\\d Q\\d{2}` (e.g. UMC uses `3Q25` like TSMC, but
    others might not).
"""

from __future__ import annotations

import re
from typing import Iterator

# ---------------------------------------------------------------------------
# Numeric tokenisation
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^[\s\(]*-?[\d,]+\.?\d*[\s\)%]*$")
_NUM_TOKEN_RE = re.compile(r"\(?-?[\d,]+\.?\d*\)?%?")
# Placeholder cells that occupy a numeric column position but carry no value
# (e.g. '-' rendered when QoQ% can't be computed because the prior period was
# negative, or 'N/A' for unreported metrics). These count as a column for
# positional alignment but yield None.
_NUM_PLACEHOLDER = {"-", "—", "N/A", "n/a"}


def _is_num_chunk(c: str) -> bool:
    return bool(_NUM_TOKEN_RE.fullmatch(c)) or c in _NUM_PLACEHOLDER


def parse_num(s: str) -> float | None:
    """'1,134.10' / '(382.80)' / '66.2%' / '-' -> 1134.10 / -382.80 / 66.2 / None."""
    s = (s or "").strip()
    if not s or s in {"-", "—", "N/A", "n/a"}:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def take_n_numbers(
    lines: list[str],
    start: int,
    n: int,
    *,
    max_label_continuations: int = 2,
) -> tuple[list[float | None], int]:
    """Walk forward from `start` and return the next `n` numeric values.

    Handles:
      - One number per line (most rows).
      - Multiple numbers on one line (e.g. " 1,134.10  1,046.09  839.25"
        — Workiva typesetter compresses dense rows into a single text run).
      - Up to `max_label_continuations` non-numeric, non-blank lines BEFORE
        the first number — covers wrapped multi-line row labels like
        "Net Income Attributable to Shareholders of the\\nParent Company".

    Stops at the first non-numeric line AFTER values have started arriving.
    Returns (values, index_after_last_consumed_line).
    """
    out: list[float | None] = []
    i = start
    started = False
    skipped_continuations = 0
    while i < len(lines) and len(out) < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        chunks = s.split()
        if all(_is_num_chunk(c) for c in chunks):
            for c in chunks:
                if len(out) >= n:
                    break
                out.append(parse_num(c))
            started = True
            i += 1
            continue
        if not started and skipped_continuations < max_label_continuations:
            skipped_continuations += 1
            i += 1
            continue
        break
    return out, i


# ---------------------------------------------------------------------------
# Section slicing
# ---------------------------------------------------------------------------

def find_section_lines(
    text: str,
    anchor: str,
    end_anchors: tuple[str, ...] = (),
) -> list[str] | None:
    """Slice `text` into lines from the line containing `anchor` up to (but
    not including) the first line that contains any of `end_anchors`.
    Returns None when `anchor` isn't found at all."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if anchor in ln), None)
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if any(e in lines[j] for e in end_anchors):
            end = j
            break
    return lines[start:end]


# ---------------------------------------------------------------------------
# Period detection
# ---------------------------------------------------------------------------
#
# Default period regex matches the "{N}Q{YY}" form used by TSMC and UMC
# (e.g. "1Q26", "3Q25"). Pass a different `period_re` if a company uses
# a different shape (e.g. "Q1 2026" or "FY26 Q1").

DEFAULT_PERIOD_RE = re.compile(r"\b(\d)Q(\d{2})\b")


def detect_periods(
    section_lines: list[str],
    *,
    max_scan: int = 30,
    period_re: re.Pattern[str] = DEFAULT_PERIOD_RE,
) -> list[str]:
    """Find the first run of >=2 consecutive period labels in the slice.
    Stops collecting at the first non-period, non-blank line after at
    least one period was found."""
    labels: list[str] = []
    started = False
    for line in section_lines[:max_scan]:
        s = line.strip()
        if not s:
            continue
        if period_re.match(s):
            labels.append(s)
            started = True
        elif started:
            break
    return labels


# ---------------------------------------------------------------------------
# The driver: label-then-N-values table parser
# ---------------------------------------------------------------------------

def parse_value_table(
    section_lines: list[str],
    row_specs: list[tuple[str, str, str]],
    period_labels: list[str] | None = None,
    *,
    period_re: re.Pattern[str] = DEFAULT_PERIOD_RE,
) -> Iterator[tuple[str, str, float, str]]:
    """Yields (metric, period_label, value, unit) for each row matched.

    `row_specs` = [(label_regex, metric_name, unit), ...].

    If `period_labels` is None, periods are auto-detected via `detect_periods`
    inside the slice.

    For each row in `row_specs`, the first line where the regex matches is
    the row label. The next `len(period_labels)` numeric values are taken
    via `take_n_numbers`, paired with the period labels in order, and
    yielded.
    """
    if period_labels is None:
        period_labels = detect_periods(section_lines, period_re=period_re)
        if not period_labels:
            return
    for label_pat, metric, unit in row_specs:
        rgx = re.compile(label_pat, re.MULTILINE)
        idx = next((i for i, line in enumerate(section_lines) if rgx.search(line)), None)
        if idx is None:
            continue
        values, _ = take_n_numbers(section_lines, idx + 1, len(period_labels))
        for plabel, val in zip(period_labels, values):
            if val is not None:
                yield metric, plabel, val, unit
