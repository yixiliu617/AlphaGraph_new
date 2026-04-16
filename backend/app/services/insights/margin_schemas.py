"""
margin_schemas.py -- Pydantic models for MarginInsightsService.

Shared between the service, the API response, and the LLM structured-output
schema. A single source of truth so the prompt, cache, and frontend all agree.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Direction    = Literal["positive", "negative"]
CurrentState = Literal["strengthening", "steady", "weakening", "unclear"]
MarginType   = Literal["gross", "operating", "net"]
DocType      = Literal["8-K", "10-Q", "10-K", "note"]


class SourceRef(BaseModel):
    index: int
    title: str
    doc_type: DocType
    date: str
    url: str | None = None


class Factor(BaseModel):
    label: str = Field(..., description="Short driver name, e.g. 'AI data-center mix shift'")
    direction: Direction
    evidence: str = Field(..., description="One-sentence quote or paraphrase from the source")
    source_ref: int = Field(
        ...,
        description="Index into sources[]; -1 = LLM background knowledge; -2 = user-added",
    )
    user_edited: bool = Field(
        default=False,
        description="True if the user has overridden this factor's content",
    )
    deleted: bool = Field(
        default=False,
        description="Soft delete -- factor is hidden but kept for undo / audit",
    )


class PeakTroughNarrative(BaseModel):
    period: str = Field(..., description="Fiscal label, e.g. 'FY2025-Q3'")
    value_pct: float
    factors: list[Factor] = Field(default_factory=list)


class FactorStatus(BaseModel):
    factor: str = Field(..., description="Matches a historical factor label")
    current_state: CurrentState
    evidence: str


class CurrentRead(BaseModel):
    summary: str = Field(..., description="2-3 sentence synthesis of today's margin setup")
    positive_factors_status: list[FactorStatus] = Field(default_factory=list)
    negative_factors_status: list[FactorStatus] = Field(default_factory=list)
    user_edited_summary: bool = Field(
        default=False,
        description="True if the user has rewritten the summary",
    )


class MarginNarrative(BaseModel):
    margin_type: MarginType
    peak: PeakTroughNarrative
    trough: PeakTroughNarrative
    current_situation: CurrentRead


class MarginInsights(BaseModel):
    ticker: str
    generated_at: str = Field(..., description="ISO-8601 timestamp")
    period_end: str = Field(..., description="Latest period end at generation time; cache key")
    margins: list[MarginNarrative]
    sources: list[SourceRef] = Field(default_factory=list)
    disclaimer: str = (
        "Generated from public filings and news. Not investment advice."
    )
