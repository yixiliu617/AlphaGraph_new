"""
Module 3: Company Business Intelligence Extractor
==================================================

Reads a broker report PDF, identifies the primary company and any directly
compared peers, then extracts one comprehensive business-intelligence fragment
per company covering: all segments, products, revenue breakdown, key metrics,
and exact verbatim sentences.

Pipeline (6 steps):
  1. load_document         — PDF -> content pages (disclosure filter applied)
  2. identify_companies    — LLM on first 3 pages -> primary + peer company list
                             stored in ctx.identified_entities
  3. extract_per_company   — full-document LLM call per company -> llm_outputs
  4. build_company_fragments — one DataFragment per company; peer skipped if
                             no segment or metric data found
  5. store_fragments       — save to DB + embed to Pinecone
  6. fanout_to_graph       — HAS_SEGMENT, HAS_PRODUCT, COMPARED_TO edges

Per-company fields stored in extracted_metrics:
  company_ticker            — stock ticker or short name
  company_name              — full company name
  is_primary                — True for the primary report subject
  compared_to_primary       — ticker of primary company (peer fragments only)
  business_summary          — 2-3 sentence overview of the company's business
  segments                  — list of business segments (name, revenue, pct_of_total,
                              growth_rate, trend, key_drivers, verbatim_quotes)
  products                  — list of key products / product lines
  key_metrics               — dict of financial metrics with value + unit + period
  competitive_position      — plain-English description vs. primary (peers only)
  verbatim_quotes           — list of exact sentences from the document about this company

Document-level provenance (same for all fragments from this document):
  source_article_title
  source_article_author
  source_article_date
  source_document_id
  source_pdf_filename

Graph edges written:
  (Company ticker) -[:HAS_SEGMENT]-> (Segment name)
  (Segment name)   -[:HAS_PRODUCT]->(Product name)
  (Peer ticker)    -[:COMPARED_TO]-> (Primary ticker)
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


# ---------------------------------------------------------------------------
# Identification schema — lightweight, first-pass (internal use only)
# ---------------------------------------------------------------------------

_COMPANY_ID_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "primary_company": {
            "type": "object",
            "description": "The main company this report is about",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker or short identifier"},
                "name":   {"type": "string", "description": "Full company name"},
            },
            "required": ["ticker", "name"],
        },
        "peer_companies": {
            "type": "array",
            "description": "Companies explicitly compared or contrasted to the primary company",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "name":   {"type": "string"},
                },
                "required": ["ticker", "name"],
            },
        },
    },
    "required": ["primary_company", "peer_companies"],
}


# ---------------------------------------------------------------------------
# Per-company extraction recipe
# ---------------------------------------------------------------------------

COMPANY_INTEL_RECIPE: dict = {
    "name": "AlphaGraph Company Business Intelligence Extractor",
    "ingestor_type": "COMPANY_BUSINESS_INTEL",
    "llm_prompt_template": (
        "You are a financial analyst extracting detailed business intelligence "
        "about a specific company from a broker research report. "
        "Extract ALL available information about the TARGET COMPANY specified below. "
        "Capture every business segment with its revenue figures, percentage of total "
        "revenue, growth rates, and key drivers. "
        "For each segment list key products or product lines. "
        "Extract key financial metrics with exact numbers and periods (e.g., "
        "'Gross margin = 58.5%, Q3 2024'). "
        "For verbatim_quotes: copy 1-3 sentences EXACTLY as they appear in the document "
        "that describe this company's business, segments, or metrics. Do not paraphrase. "
        "If the target company is a peer being compared to the primary company, "
        "also capture how it is positioned relative to the primary."
    ),
    "expected_schema": {
        "type": "object",
        "properties": {
            "company_ticker": {
                "type": "string",
                "description": "Stock ticker or short identifier for the target company",
            },
            "company_name": {
                "type": "string",
                "description": "Full company name",
            },
            "business_summary": {
                "type": "string",
                "description": "2-3 sentence overview of the company's core business",
            },
            "segments": {
                "type": "array",
                "description": "Business segments / revenue divisions",
                "items": {
                    "type": "object",
                    "properties": {
                        "segment_name":    {"type": "string"},
                        "revenue":         {"type": "string", "description": "Absolute revenue figure with currency and period, e.g. '$12.3B, FY2024'"},
                        "pct_of_total":    {"type": "string", "description": "Percentage of total company revenue, e.g. '60%'"},
                        "growth_rate":     {"type": "string", "description": "YoY or sequential growth rate, e.g. '+22% YoY'"},
                        "trend":           {"type": "string", "enum": ["accelerating", "decelerating", "stable", "unknown"]},
                        "key_drivers":     {"type": "string", "description": "1-2 sentences on what drives this segment"},
                        "verbatim_quotes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exact sentences from the document about this segment",
                        },
                    },
                    "required": ["segment_name"],
                },
            },
            "products": {
                "type": "array",
                "description": "Key products or product lines mentioned for this company",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_name":  {"type": "string"},
                        "segment":       {"type": "string", "description": "Which segment this product belongs to"},
                        "description":   {"type": "string"},
                        "metrics":       {"type": "string", "description": "Any quantitative data about this product"},
                    },
                    "required": ["product_name"],
                },
            },
            "key_metrics": {
                "type": "object",
                "description": "Financial or operational metrics with exact values",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "value":  {"type": "string", "description": "Exact numeric value with unit, e.g. '58.5%'"},
                        "period": {"type": "string", "description": "Time period, e.g. 'Q3 2024' or 'FY2024'"},
                        "trend":  {"type": "string", "description": "Direction of change, e.g. '+3.2pp YoY'"},
                    },
                },
            },
            "competitive_position": {
                "type": "string",
                "description": "For peer companies: how this company is positioned vs the primary. Leave blank for primary.",
            },
            "verbatim_quotes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 key sentences copied EXACTLY from the document about this company overall",
            },
        },
        "required": ["company_ticker", "company_name", "segments"],
    },
}


def make_company_intel_recipe(tenant_id: str) -> ExtractionRecipe:
    return ExtractionRecipe(
        tenant_id=tenant_id,
        name=COMPANY_INTEL_RECIPE["name"],
        ingestor_type=COMPANY_INTEL_RECIPE["ingestor_type"],
        llm_prompt_template=COMPANY_INTEL_RECIPE["llm_prompt_template"],
        expected_schema=COMPANY_INTEL_RECIPE["expected_schema"],
    )


# ---------------------------------------------------------------------------
# Fragment raw_text builder
# ---------------------------------------------------------------------------

def _build_raw_text(output: dict, doc_meta: dict, is_primary: bool) -> str:
    """
    Builds Pinecone-embeddable raw_text for one company fragment.
    Leads with document provenance + company summary, then segment detail.
    """
    title   = doc_meta.get("document_title", "")
    author  = doc_meta.get("document_author", "")
    date    = doc_meta.get("document_date", "")

    ticker  = output.get("company_ticker", "")
    name    = output.get("company_name", "")
    summary = output.get("business_summary", "")
    role    = "Primary subject" if is_primary else "Peer / comparative subject"

    lines = []
    if title:  lines.append(f"Source: {title}")
    if author: lines.append(f"Author: {author}")
    if date:   lines.append(f"Date: {date}")
    lines.append(f"Company: {ticker} ({name}) | {role}")
    if summary: lines.append(f"Summary: {summary}")
    lines.append("")

    for seg in output.get("segments", []):
        seg_name = seg.get("segment_name", "")
        revenue  = seg.get("revenue", "")
        pct      = seg.get("pct_of_total", "")
        growth   = seg.get("growth_rate", "")
        drivers  = seg.get("key_drivers", "")
        parts    = [seg_name]
        if revenue: parts.append(f"Rev: {revenue}")
        if pct:     parts.append(f"{pct} of total")
        if growth:  parts.append(f"Growth: {growth}")
        lines.append("Segment: " + " | ".join(parts))
        if drivers: lines.append(f"  Drivers: {drivers}")
        for q in seg.get("verbatim_quotes", []):
            lines.append(f'  Verbatim: "{q}"')
        lines.append("")

    for metric, data in output.get("key_metrics", {}).items():
        val    = data.get("value", "") if isinstance(data, dict) else str(data)
        period = data.get("period", "") if isinstance(data, dict) else ""
        trend  = data.get("trend", "") if isinstance(data, dict) else ""
        line   = f"Metric: {metric} = {val}"
        if period: line += f" ({period})"
        if trend:  line += f" [{trend}]"
        lines.append(line)

    competitive = output.get("competitive_position", "")
    if competitive:
        lines.append("")
        lines.append(f"Competitive position: {competitive}")

    for q in output.get("verbatim_quotes", []):
        lines.append(f'Key quote: "{q}"')

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Pipeline steps (company-intel-specific)
# ---------------------------------------------------------------------------

def _step_identify_companies(ctx: ExtractionContext) -> None:
    """
    Runs a lightweight LLM call on the first 3 pages of the document to
    identify the primary company and directly compared peer companies.
    Stores results in ctx.identified_entities as a list of dicts:
      [{"ticker": ..., "name": ..., "is_primary": True/False}]
    """
    # Use first 3 content pages (or fewer if the document is short)
    first_pages = ctx.pages[:3]
    text_sample = "\n\n".join(f"[Page {pn}]\n{text}" for pn, text in first_pages)

    id_prompt = (
        "You are reading the first pages of a broker research report. "
        "Identify: (1) the PRIMARY company that this report is about, and "
        "(2) any PEER companies explicitly named and compared or contrasted to the primary. "
        "Only include companies that are directly compared — not passing mentions. "
        "Output as JSON.\n\n"
        f"TEXT:\n{text_sample}"
    )

    try:
        result = ctx.llm.generate_structured_output(
            prompt=id_prompt,
            output_schema=_COMPANY_ID_SCHEMA,
        )
    except Exception as e:
        print(f"  [identify] LLM error: {e} — skipping module")
        return

    primary = result.get("primary_company", {})
    peers   = result.get("peer_companies", [])

    if not primary.get("ticker"):
        print("  [identify] No primary company found — skipping module")
        return

    ctx.identified_entities = [
        {"ticker": primary["ticker"], "name": primary.get("name", ""), "is_primary": True},
    ]
    for peer in peers:
        if peer.get("ticker"):
            ctx.identified_entities.append(
                {"ticker": peer["ticker"], "name": peer.get("name", ""), "is_primary": False}
            )

    primary_ticker = primary["ticker"]
    peer_tickers   = [p["ticker"] for p in peers if p.get("ticker")]
    print(f"  Primary: {primary_ticker} | Peers: {peer_tickers or 'none'}")


def _step_extract_per_company(ctx: ExtractionContext) -> None:
    """
    For each identified company, runs a focused LLM extraction over the
    FULL document text. Appends one output dict per company to ctx.llm_outputs,
    tagged with _ticker, _is_primary, and _primary_ticker.
    """
    if not ctx.identified_entities:
        print("  No entities to extract — skipping")
        return

    full_text = "\n\n".join(f"[Page {pn}]\n{text}" for pn, text in ctx.pages)
    primary_ticker = next(
        (e["ticker"] for e in ctx.identified_entities if e["is_primary"]), ""
    )

    prompt_prefix = (
        f"{ctx.recipe.llm_prompt_template}\n\n"
        "IMPORTANT: Copy verbatim_quotes EXACTLY word-for-word from the TEXT below. "
        "Target company is specified at the top. Output valid JSON matching the schema.\n\n"
    )

    for entity in ctx.identified_entities:
        ticker     = entity["ticker"]
        name       = entity.get("name", ticker)
        is_primary = entity["is_primary"]

        role_note = (
            "This is the PRIMARY subject of the report."
            if is_primary
            else f"This is a PEER company being compared to {primary_ticker}."
        )

        prompt = (
            prompt_prefix
            + f"TARGET COMPANY: {ticker} ({name})\n"
            + f"ROLE: {role_note}\n\n"
            + "FULL DOCUMENT TEXT:\n"
            + full_text
        )

        try:
            output = ctx.llm.generate_structured_output(
                prompt=prompt,
                output_schema=ctx.recipe.expected_schema,
            )
        except Exception as e:
            print(f"  [{ticker}] LLM error: {e} — skipped")
            continue

        # Skip peer fragments with zero substance
        has_segments = bool(output.get("segments"))
        has_metrics  = bool(output.get("key_metrics"))
        if not is_primary and not has_segments and not has_metrics:
            print(f"  [{ticker}] peer has no segment or metric data — skipped")
            continue

        # Tag with pipeline metadata
        output["_ticker"]          = ticker
        output["_is_primary"]      = is_primary
        output["_primary_ticker"]  = primary_ticker

        seg_count    = len(output.get("segments", []))
        metric_count = len(output.get("key_metrics") or {})
        print(f"  [{ticker}] {seg_count} segment(s), {metric_count} metric(s)")
        ctx.llm_outputs.append(output)


def _step_build_company_fragments(ctx: ExtractionContext) -> None:
    """
    Converts each per-company LLM output into a DataFragment.
    Peer fragments record that they were compared to the primary company.
    """
    file_name = ctx.doc_meta.get("source_pdf_filename", ctx.pdf_path.name)

    for output in ctx.llm_outputs:
        ticker         = output.get("_ticker", "unknown")
        is_primary     = output.get("_is_primary", False)
        primary_ticker = output.get("_primary_ticker", "")

        raw_text = _build_raw_text(output, ctx.doc_meta, is_primary)

        # Build clean extracted_metrics (strip pipeline-internal keys)
        extracted_metrics = {
            k: v for k, v in output.items() if not k.startswith("_")
        }
        # Inject provenance
        extracted_metrics.update({
            "is_primary":                is_primary,
            "compared_to_primary":       "" if is_primary else primary_ticker,
            "source_article_title":      ctx.doc_meta.get("document_title", ""),
            "source_article_author":     ctx.doc_meta.get("document_author", ""),
            "source_article_date":       ctx.doc_meta.get("document_date", ""),
            "source_document_id":        ctx.doc_meta.get("source_document_id", ""),
            "source_pdf_filename":       ctx.doc_meta.get("source_pdf_filename", file_name),
        })

        role_label = "primary" if is_primary else f"peer vs {primary_ticker}"
        location   = f"company:{ticker} ({role_label})"

        fragment = DataFragment(
            tenant_id=ctx.recipe.tenant_id,
            lineage=[str(ctx.recipe.recipe_id)],
            source_type=SourceType.BROKER_REPORT,
            source=file_name,
            exact_location=location,
            reason_for_extraction=(
                f"Company business intelligence extraction via '{ctx.recipe.name}' — "
                f"captures segments, products, and key metrics for {ticker} "
                f"({'primary subject' if is_primary else 'peer comparison'}) "
                f"from '{ctx.doc_meta.get('document_title', file_name)}'"
            ),
            content={"raw_text": raw_text, "extracted_metrics": extracted_metrics},
        )
        ctx.fragments.append(fragment)

    print(f"  {len(ctx.fragments)} fragment(s) built")


def _step_fanout_to_graph(ctx: ExtractionContext) -> None:
    """
    Writes graph edges for each company fragment:
      (Company) -[:HAS_SEGMENT]-> (Segment)
      (Segment) -[:HAS_PRODUCT]->(Product)
      (Peer)    -[:COMPARED_TO]-> (Primary)
    """
    total_edges = 0

    for fragment in ctx.fragments:
        metrics        = fragment.content.get("extracted_metrics", {})
        ticker         = metrics.get("company_ticker", "").strip()
        is_primary     = metrics.get("is_primary", False)
        primary_ticker = metrics.get("compared_to_primary", "").strip()
        fid            = str(fragment.fragment_id)
        src_doc        = metrics.get("source_document_id", "")

        base_meta = {
            "fragment_id":        fid,
            "source_document_id": src_doc,
            "source_document":    fragment.source,
        }

        if not ticker:
            continue

        # COMPARED_TO edge for peer companies
        if not is_primary and primary_ticker:
            ctx.graph_db.add_relationship(
                source_id=ticker,
                target_id=primary_ticker,
                relationship_type="COMPARED_TO",
                metadata={
                    **base_meta,
                    "competitive_position": metrics.get("competitive_position", ""),
                },
            )
            total_edges += 1

        # HAS_SEGMENT edges
        for seg in metrics.get("segments", []):
            seg_name = seg.get("segment_name", "").strip()
            if not seg_name:
                continue
            ctx.graph_db.add_relationship(
                source_id=ticker,
                target_id=seg_name,
                relationship_type="HAS_SEGMENT",
                metadata={
                    **base_meta,
                    "revenue":      seg.get("revenue", ""),
                    "pct_of_total": seg.get("pct_of_total", ""),
                    "growth_rate":  seg.get("growth_rate", ""),
                    "trend":        seg.get("trend", ""),
                },
            )
            total_edges += 1

            # HAS_PRODUCT edges from segment
            for prod in metrics.get("products", []):
                if prod.get("segment", "") == seg_name:
                    prod_name = prod.get("product_name", "").strip()
                    if prod_name:
                        ctx.graph_db.add_relationship(
                            source_id=seg_name,
                            target_id=prod_name,
                            relationship_type="HAS_PRODUCT",
                            metadata={
                                **base_meta,
                                "description": prod.get("description", ""),
                                "metrics":     prod.get("metrics", ""),
                            },
                        )
                        total_edges += 1

    print(f"  {total_edges} graph edge(s) written")


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

COMPANY_INTEL_PIPELINE = Pipeline(
    name="CompanyIntel",
    steps=[
        step_load_document,          # shared — PDF -> content pages
        _step_identify_companies,    # first 3 pages -> entity list
        _step_extract_per_company,   # full doc per company -> llm_outputs
        _step_build_company_fragments,  # llm_outputs -> DataFragments
        step_store_fragments,        # shared — save to DB + Pinecone
        _step_fanout_to_graph,       # HAS_SEGMENT, HAS_PRODUCT, COMPARED_TO -> Neo4j
    ],
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_company_intel_extraction(
    pdf_path: Path,
    recipe: ExtractionRecipe,
    llm: LLMProvider,
    db: DBRepository,
    vector_db: VectorRepository,
    graph_db: GraphRepository,
    doc_meta: dict,
) -> List[uuid.UUID]:
    """
    Runs the company business intelligence extraction pipeline on a broker report PDF.
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
    return COMPANY_INTEL_PIPELINE.run(ctx)
