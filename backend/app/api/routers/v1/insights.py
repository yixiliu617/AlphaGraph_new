"""
Insights API router — fully self-contained.

Routes:
  GET    /insights/templates                  list all templates (system + own)
  POST   /insights/templates                  create a custom template
  GET    /insights/templates/{template_id}    get one template
  DELETE /insights/templates/{template_id}    delete an owned template

  POST   /insights/plan                       Phase 0: parse query → ExecutionPlan
  POST   /insights/run/{insight_id}           run the confirmed plan
  POST   /insights/run-direct                 plan + run in one call (no confirmation)

  GET    /insights/{insight_id}               get an InsightOutput
  GET    /insights/entity/{ticker}            list insights for a ticker
  POST   /insights/{insight_id}/rate          approve / edit / reject

Removing this file + its dependencies.py block + its main.py line = complete removal.
"""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.models.api_contracts import APIResponse
from backend.app.models.domain.insight_models import (
    InsightTemplate,
    InsightOutput,
    ExecutionPlan,
)
from backend.app.api.dependencies import (
    get_insight_repo,
    get_insight_runner,
    get_margin_insights_service,
)
from backend.app.services.insights.margin_schemas import MarginInsights

# Edit endpoint -- request body shape
class MarginEditRequest(BaseModel):
    action:      str = Field(..., description="edit | add | delete | undo")
    margin_type: str = Field(default="gross", description="gross | operating | net")
    section:     str = Field(
        default="peak",
        description="peak | trough | current_pos | current_neg | current_summary",
    )
    factor_key:  str = Field(default="", description="Identifies the factor to edit / delete")
    period_end:  Optional[str] = Field(
        default=None,
        description="Optional override; defaults to the ticker's latest period_end",
    )
    payload:     dict = Field(default_factory=dict, description="New values for the factor")
    prev:        dict = Field(default_factory=dict, description="Pre-edit values (for audit)")

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models (insight-specific, isolated from api_contracts.py)
# ---------------------------------------------------------------------------

class CreateTemplateRequest(BaseModel):
    name: str
    description: str = ""
    intent_prompt: str
    quant_metrics: List[str] = []
    fragment_keywords: List[str] = []
    time_windows: List[str] = ["1Y", "5Y", "10Y"]
    output_formats: List[str] = ["data_table", "chart", "text_summary", "bullets"]
    chart_style: str = "AI_DECIDE"
    causation_analysis: bool = True
    web_search_fallback: bool = False
    staleness_threshold_days: int = 7
    coverage_peers_summary: bool = True
    benchmark_extremes_only: bool = True
    max_benchmark_peers: int = 30
    max_fragments: int = 100


class BuildPlanRequest(BaseModel):
    user_query: str = Field(..., description="Natural language insight request")
    template_id: uuid.UUID = Field(..., description="Which InsightTemplate to use")
    tenant_id: str = Field(..., description="The requesting tenant")


class RunInsightRequest(BaseModel):
    tenant_id: str
    force_refresh: bool = Field(
        default=False,
        description="Ignore cache and recompute even if a fresh insight exists.",
    )


class RunDirectRequest(BaseModel):
    """Plan + run in a single call — skips the confirmation step."""
    user_query: str
    template_id: uuid.UUID
    tenant_id: str
    force_refresh: bool = False


class RateInsightRequest(BaseModel):
    rating: str = Field(..., description="'approved', 'edited', or 'rejected'")
    edits: Optional[str] = Field(None, description="User's edited version (if rating='edited')")


# ---------------------------------------------------------------------------
# Template endpoints
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=APIResponse)
def list_templates(tenant_id: str, insight_repo=Depends(get_insight_repo)):
    """
    Return all templates visible to the tenant:
    6 system templates (from code) + their own private templates + public DB templates.
    System templates always appear first.
    """
    from backend.app.services.insights.system_templates import SYSTEM_TEMPLATES

    db_templates = insight_repo.list_templates(tenant_id)
    # Deduplicate: system templates are not stored in DB, so no overlap expected.
    all_templates = SYSTEM_TEMPLATES + db_templates

    return APIResponse(
        success=True,
        data=[t.model_dump() for t in all_templates],
        metadata={"total": len(all_templates), "system_count": len(SYSTEM_TEMPLATES)},
    )


@router.get("/templates/{template_id}", response_model=APIResponse)
def get_template(template_id: uuid.UUID, insight_repo=Depends(get_insight_repo)):
    from backend.app.services.insights.system_templates import SYSTEM_TEMPLATES_BY_ID

    template = SYSTEM_TEMPLATES_BY_ID.get(template_id) or insight_repo.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found.")
    return APIResponse(success=True, data=template.model_dump())


@router.post("/templates", response_model=APIResponse)
def create_template(
    request: CreateTemplateRequest,
    tenant_id: str,
    insight_repo=Depends(get_insight_repo),
):
    """Create a custom InsightTemplate for this tenant."""
    from backend.app.models.domain.insight_models import TimeHorizon, OutputFormat, SourceType

    template = InsightTemplate(
        tenant_id=tenant_id,
        name=request.name,
        description=request.description,
        is_public=False,
        intent_prompt=request.intent_prompt,
        quant_metrics=request.quant_metrics,
        fragment_keywords=request.fragment_keywords,
        time_windows=[TimeHorizon(w) for w in request.time_windows if w in {h.value for h in TimeHorizon}],
        output_formats=[OutputFormat(f) for f in request.output_formats if f in {o.value for o in OutputFormat}],
        chart_style=request.chart_style,
        causation_analysis=request.causation_analysis,
        web_search_fallback=request.web_search_fallback,
        staleness_threshold_days=request.staleness_threshold_days,
        coverage_peers_summary=request.coverage_peers_summary,
        benchmark_extremes_only=request.benchmark_extremes_only,
        max_benchmark_peers=request.max_benchmark_peers,
        max_fragments=request.max_fragments,
    )
    saved = insight_repo.save_template(template)
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to save template.")
    return APIResponse(success=True, data={"template_id": str(template.template_id)})


@router.delete("/templates/{template_id}", response_model=APIResponse)
def delete_template(
    template_id: uuid.UUID,
    tenant_id: str,
    insight_repo=Depends(get_insight_repo),
):
    """Delete a private template. System templates cannot be deleted."""
    from backend.app.services.insights.system_templates import SYSTEM_TEMPLATES_BY_ID

    if template_id in SYSTEM_TEMPLATES_BY_ID:
        raise HTTPException(status_code=403, detail="System templates cannot be deleted. Fork it instead.")
    deleted = insight_repo.delete_template(template_id, tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found or not owned by this tenant.")
    return APIResponse(success=True, data={"deleted": str(template_id)})


# ---------------------------------------------------------------------------
# Plan + Run endpoints
# ---------------------------------------------------------------------------

@router.post("/plan", response_model=APIResponse)
def build_plan(request: BuildPlanRequest, runner=Depends(get_insight_runner)):
    """
    Phase 0 only — parse query → ExecutionPlan. Does NOT fetch data.
    Returns insight_id (PENDING) + the execution plan for user confirmation.
    User then calls POST /insights/run/{insight_id} to proceed.
    """
    try:
        output, plan = runner.build_plan(
            user_query=request.user_query,
            template_id=request.template_id,
            tenant_id=request.tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return APIResponse(
        success=True,
        data={
            "insight_id":     str(output.insight_id),
            "execution_plan": plan.model_dump(),
            "status":         output.status,
        },
    )


@router.post("/run/{insight_id}", response_model=APIResponse)
def run_insight(
    insight_id: uuid.UUID,
    request: RunInsightRequest,
    runner=Depends(get_insight_runner),
):
    """
    Run the full pipeline for a PENDING InsightOutput.
    Call POST /insights/plan first to get the insight_id.
    """
    try:
        output = runner.run(
            insight_id=insight_id,
            tenant_id=request.tenant_id,
            force_refresh=request.force_refresh,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return APIResponse(success=True, data=output.model_dump())


@router.post("/run-direct", response_model=APIResponse)
def run_direct(request: RunDirectRequest, runner=Depends(get_insight_runner)):
    """
    Convenience endpoint: plan + run in one call (skips user confirmation).
    Useful for programmatic / agent-driven insight generation.
    """
    try:
        output, _ = runner.build_plan(
            user_query=request.user_query,
            template_id=request.template_id,
            tenant_id=request.tenant_id,
        )
        result = runner.run(
            insight_id=output.insight_id,
            tenant_id=request.tenant_id,
            force_refresh=request.force_refresh,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return APIResponse(success=True, data=result.model_dump())


# ---------------------------------------------------------------------------
# Output retrieval + feedback
# ---------------------------------------------------------------------------

@router.get("/entity/{ticker}", response_model=APIResponse)
def get_insights_for_entity(
    ticker: str,
    tenant_id: str,
    limit: int = 10,
    insight_repo=Depends(get_insight_repo),
):
    outputs = insight_repo.get_outputs_by_entity(tenant_id, ticker.upper(), limit)
    return APIResponse(
        success=True,
        data=[o.model_dump() for o in outputs],
        metadata={"ticker": ticker.upper(), "count": len(outputs)},
    )


@router.get("/{insight_id}", response_model=APIResponse)
def get_insight(insight_id: uuid.UUID, insight_repo=Depends(get_insight_repo)):
    output = insight_repo.get_output(insight_id)
    if not output:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return APIResponse(success=True, data=output.model_dump())


# ---------------------------------------------------------------------------
# Margin insights (Data Explorer Phase B)
# ---------------------------------------------------------------------------

@router.get("/margin/{ticker}", response_model=MarginInsights)
def get_margin_insights(
    ticker: str,
    svc=Depends(get_margin_insights_service),
):
    """
    Return cached or freshly-generated margin insights for a ticker.
    On cache miss this fetches 8-K / 10-Q MD&A / news on the fly and calls
    the LLM, which may take several seconds. Subsequent calls for the same
    (ticker, latest_period_end) are near-instant.
    """
    return svc.get(ticker.upper())


@router.post("/margin/{ticker}/refresh", response_model=MarginInsights)
def refresh_margin_insights(
    ticker: str,
    svc=Depends(get_margin_insights_service),
):
    """Force regeneration, bypassing the cache."""
    return svc.get(ticker.upper(), refresh=True)


@router.post("/margin/{ticker}/edit", response_model=MarginInsights)
def edit_margin_insights(
    ticker: str,
    request: MarginEditRequest,
    svc=Depends(get_margin_insights_service),
):
    """
    Apply a user edit (edit / add / delete / undo) and return the merged
    insights. Edits are logged append-only; the LLM baseline stays intact,
    so user edits survive any future /refresh.
    """
    try:
        return svc.apply_edit(ticker.upper(), request.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")


@router.post("/{insight_id}/rate", response_model=APIResponse)
def rate_insight(
    insight_id: uuid.UUID,
    request: RateInsightRequest,
    insight_repo=Depends(get_insight_repo),
):
    """Phase 10: record approve / edit / reject feedback."""
    valid_ratings = {"approved", "edited", "rejected"}
    if request.rating not in valid_ratings:
        raise HTTPException(status_code=400, detail=f"rating must be one of {valid_ratings}")

    updated = insight_repo.update_output_rating(insight_id, request.rating, request.edits)
    if not updated:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return APIResponse(success=True, data={"insight_id": str(insight_id), "rating": request.rating})
