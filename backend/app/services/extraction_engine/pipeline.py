"""
Extraction Pipeline — shared context and runner.

Every extraction module (causal, chart, future modules) uses:

  1. ExtractionContext  — a dataclass that carries all state through the
                          pipeline. Steps read from it and write to it.
                          No data is passed as function arguments between steps.

  2. Pipeline           — a list of step functions executed in order.
                          Each step is a plain function: step(ctx) -> None.
                          The pipeline runner handles per-step logging and
                          failure isolation.

Adding a new extraction module:
  - Write step functions that read/write ExtractionContext fields.
  - Define a Pipeline([step_a, step_b, ...]) in your extractor file.
  - Call pipeline.run(ctx) from your run_X_extraction() function.
  - Touch nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import GraphRepository, VectorRepository
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe


# ---------------------------------------------------------------------------
# Shared context — the single object that flows through every step
# ---------------------------------------------------------------------------

@dataclass
class ExtractionContext:
    """
    All state for one pipeline run.

    Inputs (set before the pipeline starts):
      pdf_path        — path to the source PDF
      doc_meta        — pre-extracted document metadata (title, author, date,
                        source_document_id, source_pdf_filename)
      recipe          — the ExtractionRecipe for this module
      db / llm /
      vector_db /
      graph_db        — pre-constructed adapters (each thread owns its own set)
      gemini_api_key  — only needed by modules that call Gemini Vision directly
      output_dir      — only needed by modules that save files to disk

    State populated by steps (start empty, filled as pipeline progresses):
      pages           — all content pages after disclosure filter
                        List[(page_num: int, text: str)]
      content_page_nums — set of page numbers in `pages` (for fast lookup)
      chunks          — 3-page text chunks for text-based LLM calls
                        List[(location_label: str, combined_text: str)]
      chart_page_nums — pages detected as containing charts
      chart_images    — rendered PNG bytes, keyed by page number
      llm_outputs     — structured dicts returned by LLM, one per chunk or page.
                        Steps may add internal metadata keys prefixed with "_":
                          "_location": str   — "pp. 1-3" or "p. 5"
                          "_page_num": int   — page number (chart modules)
      chart_files     — saved PNG paths, keyed by page number
      fragments       — fully constructed DataFragments ready to store
    """

    # --- Inputs ---
    pdf_path: Path
    doc_meta: dict
    recipe: ExtractionRecipe
    db: DBRepository
    llm: LLMProvider
    vector_db: VectorRepository
    graph_db: GraphRepository
    gemini_api_key: str = ""
    output_dir: Optional[Path] = None

    # --- State ---
    pages: List[Tuple[int, str]] = field(default_factory=list)
    content_page_nums: set = field(default_factory=set)
    chunks: List[Tuple[str, str]] = field(default_factory=list)
    chart_page_nums: List[int] = field(default_factory=list)
    chart_images: Dict[int, bytes] = field(default_factory=dict)
    llm_outputs: List[dict] = field(default_factory=list)
    chart_files: Dict[int, Path] = field(default_factory=dict)
    fragments: List[DataFragment] = field(default_factory=list)
    # General-purpose entity list — used by modules that first identify entities
    # before running per-entity extractions (e.g. company intel module).
    # Each entry is a plain dict; schema is module-defined.
    identified_entities: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Runs a list of step functions sequentially, sharing an ExtractionContext.

    Each step is a plain callable: step(ctx: ExtractionContext) -> None.

    Per-step logging is automatic — no print() needed inside steps for
    entry/exit. Steps should print only for per-item progress (e.g. per page).

    A step that raises will stop the pipeline and propagate the exception to
    the caller (run_X_extraction), which decides whether to log and continue
    or abort. This matches how the parallel runner currently handles failures.
    """

    def __init__(self, name: str, steps: List[Callable[[ExtractionContext], None]]):
        self.name = name
        self.steps = steps

    def run(self, ctx: ExtractionContext) -> List[uuid.UUID]:
        """
        Executes all steps in order. Returns the list of created fragment IDs.
        """
        file_name = ctx.pdf_path.name
        print(f"\n[{self.name}] Starting {len(self.steps)}-step pipeline on: {file_name}")

        for step_fn in self.steps:
            label = step_fn.__name__.lstrip("_").replace("step_", "").replace("_", " ")
            print(f"[{self.name}] >> {label}")
            step_fn(ctx)

        ids = [f.fragment_id for f in ctx.fragments]
        print(f"[{self.name}] Done — {len(ids)} fragment(s) created.")
        return ids
