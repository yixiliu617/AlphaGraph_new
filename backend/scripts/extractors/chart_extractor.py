"""
Module 2: Chart / Exhibit Extractor
=====================================

Detects pages containing charts/exhibits in a broker report PDF, uses
Gemini Vision to analyse each chart, saves the page as a PNG with a
descriptive filename, and stores a searchable DataFragment.

Pipeline (7 steps):
  1. load_document      — PDF → content pages (disclosure filter applied)
  2. detect_charts      — heuristic scan → chart page numbers
  3. render_images      — each chart page → PNG bytes at 200 DPI
  4. call_vision_llm    — each image + page text → structured chart data
  5. save_images        — write PNG files with {title}_{broker}_{date}.png names
  6. build_fragments    — LLM output + doc provenance → DataFragments
  7. store_fragments    — save to DB + embed to Pinecone

Per-chart fields stored in extracted_metrics:
  chart_title                       — exhibit title or fallback label
  searchable_summary                — 2-3 sentence AI summary
  verbatim_caption_or_surrounding_text — 1-2 exact sentences from the document
  relevance_reason                  — why this chart matters for financial analysis
  data_table                        — {label: value} key-value pairs from the chart
  trend_direction                   — upward | downward | flat | mixed
  trend_summary                     — trend narrative with magnitude
  range_min / range_max             — Y-axis min/max values
  y_axis_unit                       — e.g. "USD Bn", "%", "Index"
  recent_change_description         — what changed in the most recent period
  time_period_covered               — e.g. "2019-2024"
  keywords                          — 5-10 searchable terms
  chart_file_path / chart_file_name — path to saved PNG
  page_number                       — 1-indexed page in the source PDF
  source_article_*                  — document provenance fields
"""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path
from typing import List

import google.generativeai as genai
from PIL import Image

from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.enums import SourceType
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import VectorRepository
from backend.app.services.extraction_engine.pipeline import ExtractionContext, Pipeline
from backend.app.services.extraction_engine.steps.shared_steps import (
    step_load_document,
    step_store_fragments,
)

from backend.scripts.extractors.pdf_utils import (
    detect_chart_pages,
    render_page_as_png,
    extract_exhibit_title,
)


# ---------------------------------------------------------------------------
# Recipe definition
# ---------------------------------------------------------------------------

CHART_EXTRACTION_RECIPE: dict = {
    "name": "AlphaGraph Chart & Exhibit Extractor",
    "ingestor_type": "CHART_VISION",
    "llm_prompt_template": (
        "You are a financial chart analyst. Analyse this chart/exhibit image from a "
        "broker research report and extract structured data. Be precise about numbers. "
        "For verbatim_caption_or_surrounding_text: copy 1-2 sentences EXACTLY as they "
        "appear in the page text — do not paraphrase. "
        "For relevance_reason: explain in one sentence why this chart is significant "
        "for a financial analyst or investor."
    ),
    "expected_schema": {
        "type": "object",
        "properties": {
            "chart_title": {
                "type": "string",
                "description": "Full exhibit/figure title as shown in the chart or page",
            },
            "searchable_summary": {
                "type": "string",
                "description": (
                    "2-3 sentence natural language summary including chart title, key values, "
                    "trend direction, and magnitude. Optimised for semantic search."
                ),
            },
            "verbatim_caption_or_surrounding_text": {
                "type": "string",
                "description": (
                    "Copy 1-2 sentences EXACTLY word-for-word from the PAGE TEXT provided "
                    "that describe, reference, or caption this chart. Do not paraphrase."
                ),
            },
            "relevance_reason": {
                "type": "string",
                "description": "One sentence explaining why this chart is financially significant.",
            },
            "data_table": {
                "type": "object",
                "description": "Key-value data points from the chart (label -> numeric value)",
                "additionalProperties": {"type": "number"},
            },
            "trend_direction": {
                "type": "string",
                "enum": ["upward", "downward", "flat", "mixed"],
            },
            "trend_summary": {
                "type": "string",
                "description": "Concise trend narrative with magnitude",
            },
            "range_min": {"type": "number"},
            "range_max": {"type": "number"},
            "y_axis_unit": {"type": "string"},
            "recent_change_description": {"type": "string"},
            "time_period_covered": {"type": "string"},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "5-10 searchable terms describing the chart content",
            },
        },
        "required": [
            "chart_title", "searchable_summary", "verbatim_caption_or_surrounding_text",
            "relevance_reason", "trend_direction", "trend_summary", "keywords",
        ],
    },
}


def make_chart_recipe(tenant_id: str) -> ExtractionRecipe:
    return ExtractionRecipe(
        tenant_id=tenant_id,
        name=CHART_EXTRACTION_RECIPE["name"],
        ingestor_type=CHART_EXTRACTION_RECIPE["ingestor_type"],
        llm_prompt_template=CHART_EXTRACTION_RECIPE["llm_prompt_template"],
        expected_schema=CHART_EXTRACTION_RECIPE["expected_schema"],
    )


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

_UNSAFE_CHARS  = re.compile(r'[^\w\s\-]')
_WHITESPACE    = re.compile(r'\s+')
_BROKER_FILLER = re.compile(
    r'\b(global|investment|research|equity|securities|capital|markets|'
    r'group|bank|management|asset|financial|fund|partners|advisory|and|&|the)\b',
    re.IGNORECASE,
)


def _safe_filename(title: str, max_len: int = 60) -> str:
    s = _UNSAFE_CHARS.sub("", title)
    s = _WHITESPACE.sub("_", s.strip())
    return s[:max_len].rstrip("_")


def _safe_broker_name(author: str, max_len: int = 20) -> str:
    if not author or author.strip().lower() in ("", "unknown"):
        return "Unknown"
    s = _BROKER_FILLER.sub("", author)
    s = _UNSAFE_CHARS.sub("", s)
    s = _WHITESPACE.sub("", s.strip())
    return s[:max_len] or "Unknown"


def _format_date_for_filename(date_str: str) -> str:
    if not date_str or date_str.strip().lower() in ("", "unknown"):
        return "Unknown"
    compact = re.sub(r'[-/]', '', date_str.strip())
    compact = re.sub(r'\D', '', compact)
    return compact[:8] if compact else "Unknown"


# ---------------------------------------------------------------------------
# Gemini Vision helper
# ---------------------------------------------------------------------------

_VISION_PROMPT_TEMPLATE = """\
Analyse this financial chart/exhibit from a broker research report.

PAGE TEXT (exact text extracted from this page — use this for verbatim_caption_or_surrounding_text):
---
{page_text}
---

JSON Schema to follow exactly:
{schema}

Instructions:
1. chart_title: copy the exact exhibit/figure title shown
2. searchable_summary: 2-3 sentences with title, key values, trend, and magnitude
3. verbatim_caption_or_surrounding_text: 1-2 sentences copied WORD FOR WORD from PAGE TEXT above
4. relevance_reason: one sentence — why does this chart matter to a financial analyst?
5. data_table: all visible data points as {{label: numeric_value}} pairs
6. trend_direction: "upward", "downward", "flat", or "mixed"
7. trend_summary: one sentence with trend magnitude
8. range_min / range_max: approximate Y-axis min and max values
9. y_axis_unit: the Y-axis unit label
10. recent_change_description: what changed in the most recent period shown
11. time_period_covered: date range shown
12. keywords: 5-10 search terms a financial analyst would use to find this chart

Respond with JSON only. No markdown, no explanation."""


def _call_gemini_vision(
    model: genai.GenerativeModel,
    png_bytes: bytes,
    schema: dict,
    page_text: str,
) -> dict:
    img = Image.open(io.BytesIO(png_bytes))
    trimmed = page_text[:1500] if page_text else "(no text extracted from this page)"
    prompt  = _VISION_PROMPT_TEMPLATE.format(
        page_text=trimmed,
        schema=json.dumps(schema, indent=2),
    )
    response = model.generate_content(
        [prompt, img],
        generation_config=genai.GenerationConfig(response_mime_type="application/json"),
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Fragment builder helper
# ---------------------------------------------------------------------------

def _build_raw_text(
    llm_output: dict,
    doc_meta: dict,
    exhibit_title: str,
    page_num: int,
    chart_file: Path,
) -> str:
    title      = doc_meta.get("document_title", "")
    main_point = doc_meta.get("document_main_point", "")
    author     = doc_meta.get("document_author", "")
    date       = doc_meta.get("document_date", "")
    chart_title = llm_output.get("chart_title") or exhibit_title
    summary     = llm_output.get("searchable_summary", "")
    verbatim    = llm_output.get("verbatim_caption_or_surrounding_text", "")
    relevance   = llm_output.get("relevance_reason", "")
    trend       = llm_output.get("trend_summary", "")
    recent      = llm_output.get("recent_change_description", "")
    period      = llm_output.get("time_period_covered", "")
    unit        = llm_output.get("y_axis_unit", "")
    keywords    = llm_output.get("keywords", [])

    lines = []
    if title:      lines.append(f"Source: {title}")
    if author:     lines.append(f"Author: {author}")
    if date:       lines.append(f"Date: {date}")
    if main_point: lines.append(f"Document main point: {main_point}")
    lines.append("")
    lines.append(f"Chart: {chart_title}")
    lines.append(f"Page: {page_num}")
    if period: lines.append(f"Period: {period}")
    if unit:   lines.append(f"Unit: {unit}")
    lines.append("")
    if verbatim:  lines.append(f'Verbatim from document: "{verbatim}"')
    if summary:   lines.append(summary)
    if relevance: lines.append(f"Why it matters: {relevance}")
    if trend:     lines.append(f"Trend: {trend}")
    if recent:    lines.append(f"Recent change: {recent}")
    if keywords:  lines.append(f"Keywords: {', '.join(keywords)}")
    lines.append(f"Chart file: {chart_file.name}")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Pipeline steps (chart-specific)
# ---------------------------------------------------------------------------

def _step_detect_charts(ctx: ExtractionContext) -> None:
    """
    Heuristic scan to find pages likely containing charts/exhibits.
    Only considers pages within the content boundary (disclosure pages excluded).
    """
    ctx.chart_page_nums = detect_chart_pages(
        ctx.pdf_path, content_page_nums=ctx.content_page_nums
    )
    print(f"  {len(ctx.chart_page_nums)} chart page(s) detected: {ctx.chart_page_nums}")


def _step_render_images(ctx: ExtractionContext) -> None:
    """Renders each chart page to PNG bytes at 200 DPI."""
    for page_num in ctx.chart_page_nums:
        ctx.chart_images[page_num] = render_page_as_png(ctx.pdf_path, page_num, dpi=200)
    print(f"  {len(ctx.chart_images)} page(s) rendered as PNG")


def _step_call_vision_llm(ctx: ExtractionContext) -> None:
    """
    Calls Gemini Vision for each chart image + page text.
    Stores one output dict per page in ctx.llm_outputs, tagged with _page_num.
    """
    genai.configure(api_key=ctx.gemini_api_key)
    vision_model = genai.GenerativeModel("gemini-2.5-flash")
    all_text = {pn: txt for pn, txt in ctx.pages}

    for page_num in ctx.chart_page_nums:
        png_bytes = ctx.chart_images.get(page_num)
        if not png_bytes:
            continue
        try:
            page_text     = all_text.get(page_num, "")
            exhibit_title = extract_exhibit_title(page_text, page_num)
            output = _call_gemini_vision(
                vision_model,
                png_bytes,
                ctx.recipe.expected_schema,
                page_text,
            )
            output["_page_num"]     = page_num
            output["_exhibit_title"] = exhibit_title
            chart_title = output.get("chart_title") or exhibit_title
            print(f"    p.{page_num}: '{chart_title[:70]}'")
            ctx.llm_outputs.append(output)
        except Exception as e:
            print(f"    p.{page_num}: Vision LLM ERROR — {e}")
            continue

    print(f"  {len(ctx.llm_outputs)} chart(s) analysed")


def _step_save_images(ctx: ExtractionContext) -> None:
    """
    Writes each chart PNG to output_dir with the naming convention:
      {chart_title}_{broker_name}_{report_date}.png
    Stores the saved path in ctx.chart_files[page_num].
    """
    if ctx.output_dir is None:
        raise ValueError("ExtractionContext.output_dir must be set for chart extraction.")
    ctx.output_dir.mkdir(parents=True, exist_ok=True)

    safe_broker = _safe_broker_name(ctx.doc_meta.get("document_author", ""))
    safe_date   = _format_date_for_filename(ctx.doc_meta.get("document_date", ""))

    for output in ctx.llm_outputs:
        page_num      = output["_page_num"]
        exhibit_title = output.get("_exhibit_title", f"Chart_p{page_num}")
        chart_title   = output.get("chart_title") or exhibit_title
        safe_title    = _safe_filename(chart_title)
        filename      = f"{safe_title}_{safe_broker}_{safe_date}.png"
        chart_file    = ctx.output_dir / filename

        with open(chart_file, "wb") as f:
            f.write(ctx.chart_images[page_num])

        ctx.chart_files[page_num] = chart_file
        print(f"    Saved: {filename}")

    print(f"  {len(ctx.chart_files)} PNG file(s) saved → {ctx.output_dir}")


def _step_build_chart_fragments(ctx: ExtractionContext) -> None:
    """
    Converts each chart LLM output into a DataFragment with full provenance.
    """
    file_name = ctx.doc_meta.get("source_pdf_filename", ctx.pdf_path.name)

    for output in ctx.llm_outputs:
        page_num      = output["_page_num"]
        exhibit_title = output.get("_exhibit_title", f"Chart_p{page_num}")
        chart_file    = ctx.chart_files.get(page_num, Path(f"p{page_num}.png"))

        raw_text = _build_raw_text(output, ctx.doc_meta, exhibit_title, page_num, chart_file)

        extracted_metrics = {
            **{k: v for k, v in output.items() if not k.startswith("_")},
            "chart_file_path":           str(chart_file),
            "chart_file_name":           chart_file.name,
            "page_number":               page_num,
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
            source=chart_file.name,
            exact_location=f"p. {page_num}",
            reason_for_extraction=(
                f"Chart/exhibit extraction via '{ctx.recipe.name}' — captures visual data "
                f"from '{ctx.doc_meta.get('document_title', file_name)}' as a searchable "
                f"fragment with AI-generated trend analysis and verbatim captions."
            ),
            content={"raw_text": raw_text, "extracted_metrics": extracted_metrics},
        )
        ctx.fragments.append(fragment)

    print(f"  {len(ctx.fragments)} fragment(s) built")


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

CHART_PIPELINE = Pipeline(
    name="Chart",
    steps=[
        step_load_document,       # shared — PDF → content pages
        _step_detect_charts,      # heuristic scan → chart page numbers
        _step_render_images,      # each page → PNG bytes at 200 DPI
        _step_call_vision_llm,    # Gemini Vision → structured chart data
        _step_save_images,        # write PNG files to disk
        _step_build_chart_fragments,  # LLM output → DataFragments
        step_store_fragments,     # shared — save to DB + Pinecone
    ],
)


# ---------------------------------------------------------------------------
# Public entry point (interface unchanged — run_parallel_extraction.py calls this)
# ---------------------------------------------------------------------------

def run_chart_extraction(
    pdf_path: Path,
    recipe: ExtractionRecipe,
    gemini_api_key: str,
    llm: LLMProvider,
    db: DBRepository,
    vector_db: VectorRepository,
    output_dir: Path,
    doc_meta: dict,
    source_name: str | None = None,
) -> List[uuid.UUID]:
    """
    Runs the chart extraction pipeline on a broker report PDF.
    Returns the list of created fragment_ids.
    Thread-safe: creates its own Gemini model instance, opens its own PDF handle.
    """
    ctx = ExtractionContext(
        pdf_path=Path(pdf_path),
        doc_meta=doc_meta,
        recipe=recipe,
        db=db,
        llm=llm,
        vector_db=vector_db,
        graph_db=None,         # chart module does not use the graph
        gemini_api_key=gemini_api_key,
        output_dir=Path(output_dir),
    )
    return CHART_PIPELINE.run(ctx)
