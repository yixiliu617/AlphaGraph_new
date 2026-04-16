"""
Module 4: Business Relationship Extractor
=========================================

Reads a broker report PDF, extracts all inter-company relationships mentioned
in the document (supply chain, customer relationships, competition, partnerships,
and general co-mentions), and stores them as DataFragments in the DB + vector
store + graph.

Pipeline (6 steps):
  1. load_document        — PDF -> content pages (disclosure filter applied)
  2. chunk_pages          — pages -> 3-page text chunks
  3. call_relationship_llm — each chunk -> structured relationship list via LLM
  4. build_rel_fragments  — LLM output + doc provenance -> DataFragments
  5. store_fragments      — save to DB + embed to Pinecone
  6. fanout_to_graph      — write typed relationship edges to Neo4j

Relationship types extracted:
  SUPPLIES_TO      — company A supplies components/services to company B
  CUSTOMER_OF      — company A is a customer of company B
  COMPETES_WITH    — companies in the same market competing for share
  PARTNERS_WITH    — joint ventures, co-development, licensing, alliances
  MENTIONED_WITH   — general co-mention with commentary (catch-all)

Per-relationship fields stored in extracted_metrics.relationships[]:
  source_company        — company initiating or originating the relationship
  target_company        — company on the receiving end
  relationship_type     — one of the five types above
  direction_note        — plain-English qualifier, e.g. "TSM supplies chips TO NVDA"
  commentary            — 1-2 sentences of business context for this relationship
  sentiment             — positive | negative | neutral
  timeframe             — current | near_term | medium_term | long_term | historical
  confidence            — high | medium | low
  verbatim_quote        — 1-2 sentences copied EXACTLY from the document
  relevance_reason      — why this relationship matters financially

Document-level provenance (same for all chunks):
  source_article_title
  source_article_author
  source_article_date
  source_document_id
  source_pdf_filename

Graph edges written (all directed: source -> target):
  (source_company) -[:SUPPLIES_TO]->(target_company)
  (source_company) -[:CUSTOMER_OF]->(target_company)
  (source_company) -[:COMPETES_WITH]->(target_company)
  (source_company) -[:PARTNERS_WITH]->(target_company)
  (source_company) -[:MENTIONED_WITH]->(target_company)
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

RELATIONSHIP_RECIPE: dict = {
    "name": "AlphaGraph Business Relationship Extractor",
    "ingestor_type": "BUSINESS_RELATIONSHIP",
    "llm_prompt_template": (
        "You are a financial analyst specialising in inter-company relationships. "
        "Extract EVERY relationship between named companies stated or implied in this text. "
        "Focus on: supply chain links ('TSM manufactures chips for NVDA'), "
        "customer relationships ('AWS is a major customer of AMZN'), "
        "competitive dynamics ('AMD competes directly with INTC in server CPUs'), "
        "partnerships and alliances ('MSFT and OpenAI have an exclusive partnership'), "
        "and any notable co-mention with substantive commentary. "
        "For verbatim_quote: copy 1-2 sentences EXACTLY as they appear in the text. "
        "For relevance_reason: explain in one sentence why this relationship matters "
        "to a financial analyst or investor."
    ),
    "expected_schema": {
        "type": "object",
        "properties": {
            "document_entity": {
                "type": "string",
                "description": "Primary company or sector this chunk covers",
            },
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_company": {
                            "type": "string",
                            "description": "Company initiating or originating the relationship (ticker or name)",
                        },
                        "target_company": {
                            "type": "string",
                            "description": "Company on the receiving end (ticker or name)",
                        },
                        "relationship_type": {
                            "type": "string",
                            "enum": [
                                "SUPPLIES_TO",
                                "CUSTOMER_OF",
                                "COMPETES_WITH",
                                "PARTNERS_WITH",
                                "MENTIONED_WITH",
                            ],
                        },
                        "direction_note": {
                            "type": "string",
                            "description": "Short plain-English qualifier of the relationship direction",
                        },
                        "commentary": {
                            "type": "string",
                            "description": "1-2 sentences of business context for this relationship",
                        },
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "timeframe": {
                            "type": "string",
                            "enum": ["current", "near_term", "medium_term", "long_term", "historical"],
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "verbatim_quote": {
                            "type": "string",
                            "description": "1-2 sentences copied EXACTLY word-for-word from the document",
                        },
                        "relevance_reason": {
                            "type": "string",
                            "description": "One sentence: why this relationship is financially significant",
                        },
                    },
                    "required": [
                        "source_company", "target_company", "relationship_type",
                        "commentary", "sentiment", "confidence", "verbatim_quote",
                    ],
                },
            },
        },
        "required": ["document_entity", "relationships"],
    },
}


def make_relationship_recipe(tenant_id: str) -> ExtractionRecipe:
    return ExtractionRecipe(
        tenant_id=tenant_id,
        name=RELATIONSHIP_RECIPE["name"],
        ingestor_type=RELATIONSHIP_RECIPE["ingestor_type"],
        llm_prompt_template=RELATIONSHIP_RECIPE["llm_prompt_template"],
        expected_schema=RELATIONSHIP_RECIPE["expected_schema"],
    )


# ---------------------------------------------------------------------------
# Fragment raw_text builder
# ---------------------------------------------------------------------------

def _build_raw_text(llm_output: dict, doc_meta: dict, location: str) -> str:
    """
    Builds Pinecone-embeddable raw_text for one relationship chunk fragment.
    """
    title   = doc_meta.get("document_title", "")
    author  = doc_meta.get("document_author", "")
    date    = doc_meta.get("document_date", "")
    entity  = llm_output.get("document_entity", "")
    rels    = llm_output.get("relationships", [])

    lines = []
    if title:  lines.append(f"Source: {title}")
    if author: lines.append(f"Author: {author}")
    if date:   lines.append(f"Date: {date}")
    lines.append(f"Section: {location} | Primary subject: {entity}")
    lines.append("")

    for rel in rels:
        src      = rel.get("source_company", "")
        tgt      = rel.get("target_company", "")
        rel_type = rel.get("relationship_type", "MENTIONED_WITH")
        comment  = rel.get("commentary", "")
        verbatim = rel.get("verbatim_quote", "")
        relevance= rel.get("relevance_reason", "")
        sentiment= rel.get("sentiment", "").upper()

        lines.append(f"[{rel_type}] [{sentiment}] {src} -> {tgt}")
        if comment:  lines.append(f"  Context: {comment}")
        if verbatim: lines.append(f'  Verbatim: "{verbatim}"')
        if relevance:lines.append(f"  Why it matters: {relevance}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Pipeline steps (relationship-specific)
# ---------------------------------------------------------------------------

def _step_chunk_pages(ctx: ExtractionContext) -> None:
    """Groups content pages into 3-page text chunks for LLM processing."""
    ctx.chunks = chunk_pages(ctx.pages, pages_per_chunk=3)
    print(f"  {len(ctx.pages)} page(s) -> {len(ctx.chunks)} chunk(s)")


def _step_call_relationship_llm(ctx: ExtractionContext) -> None:
    """
    Sends each text chunk to the LLM with the relationship extraction prompt.
    Chunks with no relationships found are skipped.
    """
    prompt_prefix = (
        f"{ctx.recipe.llm_prompt_template}\n\n"
        "IMPORTANT: For verbatim_quote, copy the exact words from the TEXT TO ANALYZE "
        "below. Do not rephrase. Output valid JSON matching the schema exactly.\n\n"
        "TEXT TO ANALYZE:\n"
    )

    for location, chunk_text in ctx.chunks:
        try:
            output = ctx.llm.generate_structured_output(
                prompt=prompt_prefix + chunk_text,
                output_schema=ctx.recipe.expected_schema,
            )
            rels = output.get("relationships", [])
            if not rels:
                print(f"    {location}: no relationships — skipped")
                continue
            print(f"    {location}: {len(rels)} relationship(s)")
            output["_location"] = location
            ctx.llm_outputs.append(output)
        except Exception as e:
            print(f"    {location}: LLM ERROR — {e}")
            continue


def _step_build_rel_fragments(ctx: ExtractionContext) -> None:
    """
    Converts each LLM output into a DataFragment, injecting document
    provenance from doc_meta.
    """
    file_name = ctx.doc_meta.get("source_pdf_filename", ctx.pdf_path.name)

    for output in ctx.llm_outputs:
        location = output.get("_location", "unknown")
        raw_text = _build_raw_text(output, ctx.doc_meta, location)

        extracted_metrics = {
            **{k: v for k, v in output.items() if not k.startswith("_")},
            "source_article_title":  ctx.doc_meta.get("document_title", ""),
            "source_article_author": ctx.doc_meta.get("document_author", ""),
            "source_article_date":   ctx.doc_meta.get("document_date", ""),
            "source_document_id":    ctx.doc_meta.get("source_document_id", ""),
            "source_pdf_filename":   ctx.doc_meta.get("source_pdf_filename", file_name),
        }

        fragment = DataFragment(
            tenant_id=ctx.recipe.tenant_id,
            lineage=[str(ctx.recipe.recipe_id)],
            source_type=SourceType.BROKER_REPORT,
            source=file_name,
            exact_location=location,
            reason_for_extraction=(
                f"Business relationship extraction via '{ctx.recipe.name}' — "
                f"identifies supply chain, competitive, and partnership links for "
                f"graph topology in '{ctx.doc_meta.get('document_title', file_name)}'"
            ),
            content={"raw_text": raw_text, "extracted_metrics": extracted_metrics},
        )
        ctx.fragments.append(fragment)

    print(f"  {len(ctx.fragments)} fragment(s) built")


def _step_fanout_to_graph(ctx: ExtractionContext) -> None:
    """
    Writes typed directed relationship edges to Neo4j for every extracted
    inter-company relationship.
    """
    total_edges = 0

    for fragment in ctx.fragments:
        metrics = fragment.content.get("extracted_metrics", {})
        fid     = str(fragment.fragment_id)
        src_doc = metrics.get("source_document_id", "")

        for rel in metrics.get("relationships", []):
            source = rel.get("source_company", "").strip()
            target = rel.get("target_company", "").strip()
            if not source or not target:
                continue

            rel_type = rel.get("relationship_type", "MENTIONED_WITH")

            ctx.graph_db.add_relationship(
                source_id=source,
                target_id=target,
                relationship_type=rel_type,
                metadata={
                    "direction_note":   rel.get("direction_note", ""),
                    "commentary":       rel.get("commentary", ""),
                    "sentiment":        rel.get("sentiment", ""),
                    "timeframe":        rel.get("timeframe", ""),
                    "confidence":       rel.get("confidence", ""),
                    "verbatim_quote":   rel.get("verbatim_quote", ""),
                    "relevance_reason": rel.get("relevance_reason", ""),
                    "fragment_id":      fid,
                    "source_document_id": src_doc,
                    "source_document":  fragment.source,
                },
            )
            total_edges += 1

    print(f"  {total_edges} graph edge(s) written")


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

RELATIONSHIP_PIPELINE = Pipeline(
    name="Relationship",
    steps=[
        step_load_document,           # shared — PDF -> content pages
        _step_chunk_pages,            # content pages -> 3-page text chunks
        _step_call_relationship_llm,  # each chunk -> relationship list via LLM
        _step_build_rel_fragments,    # LLM output -> DataFragments
        step_store_fragments,         # shared — save to DB + Pinecone
        _step_fanout_to_graph,        # typed edges -> Neo4j
    ],
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_relationship_extraction(
    pdf_path: Path,
    recipe: ExtractionRecipe,
    llm: LLMProvider,
    db: DBRepository,
    vector_db: VectorRepository,
    graph_db: GraphRepository,
    doc_meta: dict,
) -> List[uuid.UUID]:
    """
    Runs the business relationship extraction pipeline on a broker report PDF.
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
    return RELATIONSHIP_PIPELINE.run(ctx)
