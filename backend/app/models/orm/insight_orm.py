"""
ORM models for the insights layer.

Uses the same Base as the rest of the app so SQLAlchemy tracks all tables
together. The only required change to existing code is a one-line import in
db/session.py to ensure these models are registered before create_all().
"""

from sqlalchemy import Column, String, JSON, DateTime, Float, Integer, Boolean
from datetime import datetime
import uuid

from backend.app.models.orm.fragment_orm import Base


class InsightTemplateORM(Base):
    """Persistent store for InsightTemplate (user-created + system templates)."""
    __tablename__ = "insight_templates"

    template_id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id               = Column(String, index=True, nullable=False)
    name                    = Column(String, nullable=False)
    description             = Column(String, default="")
    is_public               = Column(Boolean, default=False)
    created_at              = Column(DateTime, default=datetime.utcnow)

    intent_prompt           = Column(String, nullable=False)
    coverage_peers_summary  = Column(Boolean, default=True)
    benchmark_extremes_only = Column(Boolean, default=True)
    max_benchmark_peers     = Column(Integer, default=30)

    time_windows            = Column(JSON, default=[])
    quant_metrics           = Column(JSON, default=[])
    fragment_source_types   = Column(JSON, default=[])
    fragment_keywords       = Column(JSON, default=[])
    max_fragments           = Column(Integer, default=100)

    output_formats          = Column(JSON, default=[])
    chart_style             = Column(String, default="AI_DECIDE")

    causation_analysis      = Column(Boolean, default=True)
    web_search_fallback     = Column(Boolean, default=False)
    min_fragment_confidence = Column(Float, default=0.7)
    staleness_threshold_days= Column(Integer, default=7)


class InsightOutputORM(Base):
    """Persistent store for InsightOutput (generated insight results)."""
    __tablename__ = "insight_outputs"

    insight_id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id             = Column(String, index=True, nullable=False)
    tenant_id               = Column(String, index=True, nullable=False)
    status                  = Column(String, default="pending")

    entities                = Column(JSON, default=[])
    peer_set                = Column(JSON, default=[])
    metrics                 = Column(JSON, default=[])
    time_windows            = Column(JSON, default=[])
    execution_plan          = Column(JSON, nullable=True)

    headline                = Column(String, nullable=True)
    data_table              = Column(JSON, nullable=True)
    chart_specs             = Column(JSON, nullable=True)
    narrative               = Column(String, nullable=True)
    bullet_points           = Column(JSON, default=[])
    source_fragment_ids     = Column(JSON, default=[])
    prior_insight_id        = Column(String, nullable=True)

    confidence_score        = Column(Float, nullable=True)
    source_tier_breakdown   = Column(JSON, default={})
    corroboration_count     = Column(Integer, default=0)
    web_search_used         = Column(Boolean, default=False)
    fragment_gap_warning    = Column(String, nullable=True)

    user_rating             = Column(String, nullable=True)
    user_edits              = Column(String, nullable=True)

    created_at              = Column(DateTime, default=datetime.utcnow)
    completed_at            = Column(DateTime, nullable=True)
