"""
Shared pipeline steps — used by multiple extraction modules.

Rules for shared steps:
  - They only touch ExtractionContext fields that every module uses.
  - They never import from a specific extractor module (no circular imports).
  - Adding per-module behaviour belongs in a module-specific step, not here.
"""

from __future__ import annotations

from backend.app.services.extraction_engine.pipeline import ExtractionContext
from backend.app.services.extraction_engine.steps.store_fragment import store_fragment
from backend.scripts.extractors.pdf_utils import get_content_pages


def step_load_document(ctx: ExtractionContext) -> None:
    """
    Reads all content pages from the PDF after stripping disclosure/appendix
    sections. Populates ctx.pages and ctx.content_page_nums.

    Shared by all PDF-based extraction modules.
    """
    ctx.pages = get_content_pages(ctx.pdf_path)
    ctx.content_page_nums = {pn for pn, _ in ctx.pages}
    print(f"  {len(ctx.pages)} content page(s) loaded "
          f"(disclosure filter applied)")


def step_store_fragments(ctx: ExtractionContext) -> None:
    """
    Persists every DataFragment in ctx.fragments to the DB and vector store.
    Deduplication is handled inside db.save_fragment() via content_fingerprint.

    Shared by all extraction modules — no module-specific logic here.
    """
    stored = 0
    for fragment in ctx.fragments:
        store_fragment(fragment, ctx.db, ctx.llm, ctx.vector_db)
        stored += 1
    print(f"  {stored} fragment(s) stored to DB + vector store")
