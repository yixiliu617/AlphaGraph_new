"""
Research API router — natural-language Q&A over the earnings-release corpus
and (future) earnings-call transcripts + meeting notes.

Routes:
  POST /research/query   — run a question against the stored documents
  GET  /research/findings/{ticker}   — list all persisted findings for a ticker
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.dependencies import get_engine_llm
from backend.app.models.api_contracts import APIResponse
from backend.app.services.research.document_query_service import (
    DocumentQueryService,
)
from backend.app.services.research.schemas import (
    QueryRequest,
    SourceType,
)

router = APIRouter()

# Module-level cache keyed by id(llm) so we don't rebuild the service on
# every request but still honor dependency-injection semantics in tests.
_service_cache: dict[int, DocumentQueryService] = {}


def _get_service(llm) -> DocumentQueryService:
    key = id(llm)
    svc = _service_cache.get(key)
    if svc is None:
        svc = DocumentQueryService(llm=llm)
        _service_cache[key] = svc
    return svc


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------

class ResearchQueryBody(BaseModel):
    ticker:         str
    question:       str
    lookback_years: int = Field(default=3, ge=1, le=10)
    source_types:   list[SourceType] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/query", response_model=APIResponse)
def research_query(
    body: ResearchQueryBody,
    llm = Depends(get_engine_llm),
) -> APIResponse:
    ticker = body.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    svc = _get_service(llm)
    try:
        result = svc.query(QueryRequest(
            ticker=ticker,
            question=body.question,
            lookback_years=body.lookback_years,
            source_types=body.source_types,
        ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    return APIResponse(success=True, data=result.model_dump())
