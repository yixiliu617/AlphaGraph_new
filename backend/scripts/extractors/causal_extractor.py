"""
Module 1: Causal Relationship Extractor
========================================

Reads a broker report PDF, extracts every causal relationship mentioned
("X caused Y", "concerns on Z led to underperformance", etc.), and stores
them as DataFragments in the DB + vector store + graph.

Pipeline (6 steps):
  1. load_document      — PDF → content pages (disclosure filter applied)
  2. chunk_pages        — pages → 3-page text chunks
  3. call_text_llm      — each chunk → structured causal chains via LLM
  4. build_fragments    — LLM output + doc provenance → DataFragments
  5. store_fragments    — save to DB + embed to Pinecone
  6. fanout_to_graph    — write CAUSES edges to Neo4j

Per-chain fields stored in extracted_metrics.causal_chains[]:
  cause_entity          — triggering entity/factor
  cause_description     — plain-English description of the root cause
  effect_entity         — entity experiencing the effect
  effect_description    — what happened as a result
  direction             — positive | negative | neutral
  timeframe             — immediate | near_term | medium_term | long_term
  confidence            — high | medium | low
  verbatim_quote        — 1-2 exact sentences copied word-for-word from the document
  relevance_reason      — why this chain matters financially

Document-level fields (stored in extracted_metrics, same for all chunks):
  source_article_title      — full report title
  source_article_main_point — 1-2 sentence executive summary of the whole document
  source_article_author     — authoring firm or analyst
  source_article_date       — publication date
  source_document_id        — stable UUID5 from filename
  source_pdf_filename       — original PDF filename

Graph fanout:
  (cause_entity) -[:CAUSES {direction, timeframe, confidence, verbatim_quote, fragment_id}]-> (effect_entity)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import List

from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.enums import SourceType
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository
from backend.app.services.extraction_engine.pipeline import ExtractionContext, Pipeline
from backend.app.services.extraction_engine.steps.shared_steps import (
    step_load_document,
    step_store_fragments,
)

from backend.scripts.extractors.pdf_utils import chunk_pages


# ---------------------------------------------------------------------------
# Recipe definition
# ---------------------------------------------------------------------------

CAUSAL_RELATIONSHIP_RECIPE: dict = {
    "name": "AlphaGraph Causal Relationship Extractor",
    "ingestor_type": "CAUSAL_RELATIONSHIP",
    "llm_prompt_template": (
        "You are a financial analyst specialising in causal reasoning. "
        "Extract EVERY causal relationship stated or implied in this text. "
        "Focus on: 'X caused Y', 'due to X, Y happened', 'X led to Y', "
        "'concerns on X led to underperformance', 'as a result of X, Y'. "
        "Include macro factors, company events, sector dynamics, and management "
        "decisions that drive financial outcomes. "
        "For verbatim_quote: copy 1-2 sentences EXACTLY as they appear in the text — "
        "do not paraphrase, do not summarise. "
        "For relevance_reason: explain in one sentence why this causal link matters "
        "to a financial analyst or investor."
    ),
    "expected_schema": {
        "type": "object",
        "properties": {
            "document_entity": {
                "type": "string",
                "description": "Primary company or sector this chunk covers (e.g. 'INTC', 'Semiconductors')",
            },
            "document_date": {
                "type": "string",
                "description": "Report or event date in YYYY-MM-DD format if found in this chunk",
            },
            "causal_chains": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cause_entity": {"type": "string"},
                        "cause_description": {"type": "string"},
                        "effect_entity": {"type": "string"},
                        "effect_description": {"type": "string"},
                        "direction": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "timeframe": {
                            "type": "string",
                            "enum": ["immediate", "near_term", "medium_term", "long_term"],
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "verbatim_quote": {
                            "type": "string",
                            "description": (
                                "1-2 sentences copied EXACTLY word-for-word from the document."
                            ),
                        },
                        "relevance_reason": {
                            "type": "string",
                            "description": "One sentence: why this causal link is financially significant.",
                        },
                    },
                    "required": [
                        "cause_entity", "effect_entity", "effect_description",
                        "direction", "confidence", "verbatim_quote", "relevance_reason",
                    ],
                },
            },
        },
        "required": ["document_entity", "causal_chains"],
    },
}


def make_causal_recipe(tenant_id: str) -> ExtractionRecipe:
    return ExtractionRecipe(
        tenant_id=tenant_id,
        name=CAUSAL_RELATIONSHIP_RECIPE["name"],
        ingestor_type=CAUSAL_RELATIONSHIP_RECIPE["ingestor_type"],
        llm_prompt_template=CAUSAL_RELATIONSHIP_RECIPE["llm_prompt_template"],
        expected_schema=CAUSAL_RELATIONSHIP_RECIPE["expected_schema"],
    )


# ---------------------------------------------------------------------------
# Fragment builder helper
# ---------------------------------------------------------------------------

def _build_raw_text(llm_output: dict, doc_meta: dict, location: str) -> str:
    """
    Builds the Pinecone-embeddable raw_text. Leads with article provenance
    so semantic search on the document topic lands on every fragment from
    this document. Follows with per-chain verbatim quotes for granular retrieval.
    """
    title      = doc_meta.get("document_title", "")
    main_point = doc_meta.get("document_main_point", "")
    author     = doc_meta.get("document_author", "")
    date       = doc_meta.get("document_date", "")
    entity     = llm_output.get("document_entity", "")
    chains     = llm_output.get("causal_chains", [])

    lines = []
    if title:      lines.append(f"Source: {title}")
    if author:     lines.append(f"Author: {author}")
    if date:       lines.append(f"Date: {date}")
    if main_point: lines.append(f"Document main point: {main_point}")
    lines.append(f"Section: {location} | Primary subject: {entity}")
    lines.append("")

    for c in chains:
        direction   = c.get("direction", "").upper()
        cause_desc  = c.get("cause_description") or c.get("cause_entity", "")
        effect_desc = c.get("effect_description", "")
        verbatim    = c.get("verbatim_quote", "")
        relevance   = c.get("relevance_reason", "")
        lines.append(f"[{direction}] {cause_desc} -> {effect_desc}")
        if verbatim:  lines.append(f'  Verbatim: "{verbatim}"')
        if relevance: lines.append(f"  Why it matters: {relevance}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Pipeline steps (causal-specific)
# ---------------------------------------------------------------------------

def _step_chunk_pages(ctx: ExtractionContext) -> None:
    """Groups content pages into 3-page text chunks for LLM processing."""
    ctx.chunks = chunk_pages(ctx.pages, pages_per_chunk=3)
    print(f"  {len(ctx.pages)} page(s) -> {len(ctx.chunks)} chunk(s)")


def _step_call_text_llm(ctx: ExtractionContext) -> None:
    """
    Sends each text chunk to the LLM with the causal extraction prompt.
    Chunks that yield no causal chains are skipped (not added to llm_outputs).
    """
    prompt_prefix = (
        f"{ctx.recipe.llm_prompt_template}\n\n"
        "IMPORTANT: For verbatim_quote, copy the exact words from the TEXT TO ANALYZE "
        "below — do not rephrase. Output valid JSON matching the schema exactly.\n\n"
        "TEXT TO ANALYZE:\n"
    )

    for location, chunk_text in ctx.chunks:
        try:
            output = ctx.llm.generate_structured_output(
                prompt=prompt_prefix + chunk_text,
                output_schema=ctx.recipe.expected_schema,
            )
            chains = output.get("causal_chains", [])
            if not chains:
                print(f"    {location}: no causal chains — skipped")
                continue
            print(f"    {location}: {len(chains)} chain(s)")
            output["_location"] = location
            ctx.llm_outputs.append(output)
        except Exception as e:
            print(f"    {location}: LLM ERROR — {e}")
            continue


def _step_build_causal_fragments(ctx: ExtractionContext) -> None:
    """
    Converts each LLM output into a fully populated DataFragment,
    injecting document-level provenance from doc_meta.
    """
    file_name = ctx.doc_meta.get("source_pdf_filename", ctx.pdf_path.name)

    for output in ctx.llm_outputs:
        location = output.get("_location", "unknown")
        raw_text = _build_raw_text(output, ctx.doc_meta, location)

        extracted_metrics = {
            **{k: v for k, v in output.items() if not k.startswith("_")},
            "source_article_title":      ctx.doc_meta.get("document_title", ""),
            "source_article_main_point": ctx.doc_meta.get("document_main_point", ""),
            "source_article_author":     ctx.doc_meta.get("document_author", ""),
            "source_article_date":       ctx.doc_meta.get("document_date", ""),
            "source_document_id":        ctx.doc_meta.get("source_document_id", ""),
            "source_pdf_filename":       ctx.doc_meta.get("source_pdf_filename", file_name),
        }

        fragment = DataFragment(
            tenant_id=ctx.recipe.tenant_id,
            lineage=[str(ctx.recipe.recipe_id)],
            source_type=SourceType.BROKER_REPORT,
            source=file_name,
            exact_location=location,
            reason_for_extraction=(
                f"Causal relationship extraction via '{ctx.recipe.name}' — "
                f"identifies financial cause-effect chains for graph topology "
                f"and semantic search in '{ctx.doc_meta.get('document_title', file_name)}'"
            ),
            content={"raw_text": raw_text, "extracted_metrics": extracted_metrics},
        )
        ctx.fragments.append(fragment)

    print(f"  {len(ctx.fragments)} fragment(s) built")


def _step_fanout_to_graph(ctx: ExtractionContext) -> None:
    """
    Creates Neo4j CAUSES edges for every extracted causal chain.
    Each fragment's chains become individual directed graph edges.
    """
    total_edges = 0
    for fragment in ctx.fragments:
        metrics = fragment.content.get("extracted_metrics", {})
        for chain in metrics.get("causal_chains", []):
            cause  = chain.get("cause_entity", "").strip()
            effect = chain.get("effect_entity", "").strip()
            if not cause or not effect:
                continue
            ctx.graph_db.add_relationship(
                source_id=cause,
                target_id=effect,
                relationship_type="CAUSES",
                metadata={
                    "direction":          chain.get("direction"),
                    "timeframe":          chain.get("timeframe"),
                    "confidence":         chain.get("confidence"),
                    "cause_description":  chain.get("cause_description", ""),
                    "effect_description": chain.get("effect_description", ""),
                    "verbatim_quote":     chain.get("verbatim_quote", ""),
                    "relevance_reason":   chain.get("relevance_reason", ""),
                    "fragment_id":        str(fragment.fragment_id),
                    "source_document_id": metrics.get("source_document_id", ""),
                    "source_document":    fragment.source,
                },
            )
            total_edges += 1
    print(f"  {total_edges} graph edge(s) written")


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

CAUSAL_PIPELINE = Pipeline(
    name="Causal",
    steps=[
        step_load_document,       # shared — PDF → content pages
        _step_chunk_pages,        # content pages → 3-page text chunks
        _step_call_text_llm,      # each chunk → causal chains via LLM
        _step_build_causal_fragments,  # LLM output → DataFragments
        step_store_fragments,     # shared — save to DB + Pinecone
        _step_fanout_to_graph,    # CAUSES edges → Neo4j
    ],
)


# ---------------------------------------------------------------------------
# Public entry point (interface unchanged — run_parallel_extraction.py calls this)
# ---------------------------------------------------------------------------

def run_causal_extraction(
    pdf_path: Path,
    recipe: ExtractionRecipe,
    llm: LLMProvider,
    db: DBRepository,
    vector_db: VectorRepository,
    graph_db: GraphRepository,
    doc_meta: dict,
    source_name: str | None = None,
) -> List[uuid.UUID]:
    """
    Runs the causal extraction pipeline on a broker report PDF.
    Returns the list of created fragment_ids.
    Thread-safe: takes pre-constructed adapters, opens its own PDF handle.
    """
    ctx = ExtractionContext(
        pdf_path=Path(pdf_path),
        doc_meta=doc_meta,
        recipe=recipe,
        db=db,
        llm=llm,
        vector_db=vector_db,
        graph_db=graph_db,
    )
    return CAUSAL_PIPELINE.run(ctx)
