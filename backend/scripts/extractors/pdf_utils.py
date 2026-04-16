"""
Shared PDF utilities: text extraction and image rendering via PyMuPDF.

Used by both causal_extractor and chart_extractor so the PDF is only
opened once per extractor (each module opens its own handle — no
shared state between threads).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_pages_text(pdf_path: Path) -> List[Tuple[int, str]]:
    """
    Returns [(page_number, text), ...] for every page that has text.
    page_number is 1-indexed.
    Includes ALL pages — use get_content_pages() to exclude disclosures/appendix.
    """
    doc = fitz.open(str(pdf_path))
    pages: List[Tuple[int, str]] = []
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if text:
            pages.append((i + 1, text))
    doc.close()
    return pages


# ---------------------------------------------------------------------------
# Disclosure / appendix filtering
# ---------------------------------------------------------------------------

# Strong signals: these phrases almost always open a disclosure or appendix block.
_DISCLOSURE_STRONG = re.compile(
    r"\b("
    r"important\s+disclosures?"
    r"|analyst\s+certif"               # "Analyst Certification"
    r"|regulatory\s+disclosures?"
    r"|appendix\s+[a-z\d]"            # "Appendix A", "Appendix 1"
    r"|disclosures?\s+appendix"
    r"|investment\s+banking\s+relationships?"
    r"|research\s+disclosures?"
    r")\b",
    re.IGNORECASE,
)

# Weaker signals: alone they're not enough, but 2+ on the same page is conclusive.
_DISCLOSURE_WEAK = re.compile(
    r"\b("
    r"disclaimer"
    r"|conflicts?\s+of\s+interest"
    r"|not\s+an\s+offer\s+to\s+buy\s+or\s+sell"
    r"|past\s+performance\s+is\s+not"
    r"|all\s+rights\s+reserved"
    r"|may\s+not\s+be\s+reproduced"
    r"|intended\s+for\s+distribution\s+to"
    r"|legal\s+and\s+privacy\s+notices?"
    r")\b",
    re.IGNORECASE,
)


def _is_disclosure_page(text: str) -> bool:
    """Returns True when a page is a disclosure, legal, or appendix page."""
    if _DISCLOSURE_STRONG.search(text):
        return True
    return len(_DISCLOSURE_WEAK.findall(text)) >= 2


def get_content_pages(pdf_path: Path) -> List[Tuple[int, str]]:
    """
    Returns pages that contain substantive research content, with
    disclosure, legal, and appendix sections removed.

    Strategy: find the first page from the END that begins a continuous
    disclosure/appendix block and truncate there.  This handles both
    end-of-report disclosures (most common) and a mid-report appendix
    followed by more disclosures.

    Example:
      Pages  1-18  — research content  -> kept
      Pages 19-21  — disclosures       -> dropped
    """
    pages = extract_pages_text(pdf_path)
    if not pages:
        return pages

    # Walk backwards to find where the trailing disclosure block starts.
    cutoff = len(pages)
    for i in range(len(pages) - 1, -1, -1):
        if _is_disclosure_page(pages[i][1]):
            cutoff = i          # this page and everything after it is disclosure
        else:
            break               # first non-disclosure page from the end

    dropped = len(pages) - cutoff
    if dropped:
        last_kept = pages[cutoff - 1][0] if cutoff > 0 else 0
        print(
            f"[pdf_utils] Disclosure filter: kept pp.1-{last_kept}, "
            f"dropped {dropped} page(s) (disclosures/appendix)"
        )

    return pages[:cutoff]


def chunk_pages(
    pages: List[Tuple[int, str]],
    pages_per_chunk: int = 3,
) -> List[Tuple[str, str]]:
    """
    Groups pages into overlapping chunks for LLM context.
    Returns [(location_label, combined_text), ...]
    e.g. location_label = "pp. 1-3"
    """
    chunks: List[Tuple[str, str]] = []
    for start in range(0, len(pages), pages_per_chunk):
        group = pages[start : start + pages_per_chunk]
        page_nums = [p[0] for p in group]
        label = (
            f"p. {page_nums[0]}"
            if len(page_nums) == 1
            else f"pp. {page_nums[0]}-{page_nums[-1]}"
        )
        combined = "\n\n---\n\n".join(p[1] for p in group)
        chunks.append((label, combined))
    return chunks


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------

def render_page_as_png(pdf_path: Path, page_number: int, dpi: int = 200) -> bytes:
    """
    Renders a single page (1-indexed) as PNG bytes at the given DPI.
    Higher DPI = better quality for AI analysis.
    """
    doc = fitz.open(str(pdf_path))
    page = doc[page_number - 1]
    zoom = dpi / 72  # PDF base DPI is 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


# ---------------------------------------------------------------------------
# Chart page detection
# ---------------------------------------------------------------------------

_EXHIBIT_PATTERN = re.compile(
    r"\b(exhibit|figure|chart|fig\.)\s*\d*",
    re.IGNORECASE,
)
_AXIS_TERMS = re.compile(
    r"\b(yoy|qoq|cagr|basis\s+points?|bps|usd\s*b[nm]|eur\s*b[nm]|%|y-axis|x-axis"
    r"|indexed|rebased|lhs|rhs|source:)\b",
    re.IGNORECASE,
)


def detect_chart_pages(pdf_path: Path, content_page_nums: set[int] | None = None) -> List[int]:
    """
    Heuristic scan to identify pages likely containing charts/exhibits.

    A page is flagged when it meets any of:
      1. Text contains "Exhibit N", "Figure N", "Chart N" pattern
      2. Text is sparse (<= 300 words) AND contains axis/data terminology
      3. Page has embedded raster images AND sparse text (<= 400 words)

    Args:
      content_page_nums: if provided, only pages whose number is in this set
                         are considered (use to skip disclosure/appendix pages).

    Returns sorted list of 1-indexed page numbers.
    """
    doc = fitz.open(str(pdf_path))
    chart_pages: set[int] = set()

    for i, page in enumerate(doc):
        page_num = i + 1

        # Skip pages outside the content boundary
        if content_page_nums is not None and page_num not in content_page_nums:
            continue

        text = page.get_text("text")
        word_count = len(text.split())

        if _EXHIBIT_PATTERN.search(text):
            chart_pages.add(page_num)

        if word_count <= 300 and _AXIS_TERMS.search(text):
            chart_pages.add(page_num)

        images = page.get_images(full=False)
        if images and word_count <= 400:
            chart_pages.add(page_num)

    doc.close()
    return sorted(chart_pages)


def extract_exhibit_title(page_text: str, page_number: int) -> str:
    """
    Extracts the exhibit/figure title from page text if present,
    otherwise returns a default label.
    """
    # Try to match "Exhibit 3: AI Hardware Investment..." or "Figure 1. Revenue Growth"
    match = re.search(
        r"\b(Exhibit|Figure|Chart|Fig\.)\s*(\d+)[:\.\s]+([^\n]{5,80})",
        page_text,
        re.IGNORECASE,
    )
    if match:
        return f"{match.group(1)} {match.group(2)}: {match.group(3).strip()}"
    return f"Chart on p.{page_number}"
