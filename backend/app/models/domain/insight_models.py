"""
Insight domain models.

Completely self-contained — no changes to existing domain models required.
InsightTemplate is the insight-layer analogue of ExtractionRecipe.
InsightOutput is the insight-layer analogue of DataFragment.
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
import uuid

from backend.app.models.domain.enums import SourceType


# ---------------------------------------------------------------------------
# Insight-specific enums (do not modify enums.py)
# ---------------------------------------------------------------------------

class TimeHorizon(str, Enum):
    SHORT_1Y = "1Y"
    MID_5Y   = "5Y"
    LONG_10Y = "10Y"


class OutputFormat(str, Enum):
    DATA_TABLE   = "data_table"
    CHART        = "chart"
    TEXT_SUMMARY = "text_summary"
    BULLETS      = "bullets"


class PeerTier(str, Enum):
    COVERAGE  = "coverage"   # company is in user's coverage universe
    BENCHMARK = "benchmark"  # company is in GICS subindustry but not coverage


class InsightStatus(str, Enum):
    PENDING  = "pending"   # execution plan generated, awaiting user confirmation
    RUNNING  = "running"
    COMPLETE = "complete"
    FAILED   = "failed"


class UserRating(str, Enum):
    APPROVED = "approved"
    EDITED   = "edited"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------

class PeerWithTier(BaseModel):
    """A peer company with its coverage tier tag."""
    ticker: str
    name: str
    tier: PeerTier


class ExecutionPlan(BaseModel):
    """
    Phase 0 output — shown to the user for confirmation before any data
    fetching begins. Stored on InsightOutput so it is auditable.
    """
    entities: List[str]
    peers: List[PeerWithTier]
    metrics: List[str]
    time_windows: List[str]           # resolved horizon labels e.g. ["1Y", "5Y", "10Y"]
    source_types: List[str]           # SourceType values
    output_formats: List[str]         # OutputFormat values
    causation_analysis: bool
    web_search_allowed: bool
    expected_fragment_count: int = 0  # estimated from a quick Pinecone count


# ---------------------------------------------------------------------------
# InsightTemplate — defines HOW an insight is generated
# Analogue of ExtractionRecipe.
# ---------------------------------------------------------------------------

class InsightTemplate(BaseModel):
    """
    A reusable, parameterised definition of an insight pipeline.
    System-provided templates have tenant_id="system" and is_public=True.
    User templates have tenant_id=<user tenant> and is_public=False (default).
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    template_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str = Field(...)
    name: str = Field(...)
    description: str = Field(default="")
    is_public: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Phase 0 — intent prompt template
    # Use {entity}, {metrics}, {time_windows} as interpolation tokens.
    intent_prompt: str = Field(...)

    # Phase 2 — peer resolution
    coverage_peers_summary: bool = Field(
        default=True,
        description="Include a 1-2 sentence individual summary for each coverage-tier peer.",
    )
    benchmark_extremes_only: bool = Field(
        default=True,
        description="For benchmark-tier peers, only mention outliers (highest/lowest/diverging).",
    )
    max_benchmark_peers: int = Field(
        default=30,
        description="Cap on how many benchmark peers to fetch from the GICS subindustry.",
    )

    # Phase 3 — time windows
    time_windows: List[TimeHorizon] = Field(
        default=[TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y, TimeHorizon.LONG_10Y],
    )

    # Phase 4 — data retrieval
    quant_metrics: List[str] = Field(
        default_factory=list,
        description="Metric names to pull from DuckDB. Empty = AI decides at run time.",
    )
    fragment_source_types: List[SourceType] = Field(
        default_factory=list,
        description="Filter text fragments by source type. Empty = all source types.",
    )
    fragment_keywords: List[str] = Field(
        default_factory=list,
        description="Seed keywords for semantic fragment search.",
    )
    max_fragments: int = Field(default=100)

    # Phase 6 — output formatting
    output_formats: List[OutputFormat] = Field(
        default=[
            OutputFormat.DATA_TABLE,
            OutputFormat.CHART,
            OutputFormat.TEXT_SUMMARY,
            OutputFormat.BULLETS,
        ],
    )
    chart_style: str = Field(
        default="AI_DECIDE",
        description="Recharts chart type, e.g. 'line', 'bar', 'composed'. AI_DECIDE = LLM chooses.",
    )

    # Phase 7 — synthesis controls
    causation_analysis: bool = Field(
        default=True,
        description="Ask 'why is this metric moving?' using fragments + graph relationships.",
    )
    web_search_fallback: bool = Field(
        default=False,
        description="Allow web search when knowledge-base fragment coverage is thin. Always flagged in output.",
    )
    min_fragment_confidence: float = Field(default=0.7)
    staleness_threshold_days: int = Field(
        default=7,
        description="A cached InsightOutput older than this triggers a full recompute.",
    )

    def to_dict(self) -> dict:
        return self.model_dump()


# ---------------------------------------------------------------------------
# InsightOutput — the generated result of running a template
# Analogue of DataFragment.
# ---------------------------------------------------------------------------

class InsightOutput(BaseModel):
    """
    The persisted result of running an InsightTemplate against a set of entities.
    Created in PENDING state (execution plan only), updated to COMPLETE after synthesis.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    insight_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    template_id: uuid.UUID = Field(...)
    tenant_id: str = Field(...)
    status: InsightStatus = Field(default=InsightStatus.PENDING)

    # Resolved at Phase 0
    entities: List[str] = Field(default_factory=list)
    peer_set: List[PeerWithTier] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    time_windows: List[str] = Field(default_factory=list)

    # Execution plan stored for audit (Phase 0 output)
    execution_plan: Optional[Dict[str, Any]] = Field(default=None)

    # Generated content (populated in Phase 7+)
    headline: Optional[str] = None
    data_table: Optional[Dict[str, Any]] = None
    chart_specs: Optional[List[Dict[str, Any]]] = None
    narrative: Optional[str] = None
    bullet_points: List[str] = Field(default_factory=list)
    source_fragment_ids: List[str] = Field(default_factory=list)

    # If this insight updated a cached prior insight
    prior_insight_id: Optional[uuid.UUID] = None

    # Quality metadata (Phase 7d)
    confidence_score: Optional[float] = None
    source_tier_breakdown: Dict[str, int] = Field(default_factory=dict)
    corroboration_count: int = Field(default=0)
    web_search_used: bool = Field(default=False)
    fragment_gap_warning: Optional[str] = None

    # User feedback (Phase 10)
    user_rating: Optional[UserRating] = None
    user_edits: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
