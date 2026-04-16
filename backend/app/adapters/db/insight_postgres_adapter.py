"""
InsightPostgresAdapter — concrete implementation of InsightRepository.

Uses the same SQLAlchemy session as PostgresAdapter but is completely
independent of it. Neither class imports the other.
"""

from datetime import datetime, timedelta
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from backend.app.interfaces.insight_repository import InsightRepository
from backend.app.models.domain.insight_models import (
    InsightTemplate,
    InsightOutput,
    InsightStatus,
    PeerWithTier,
    PeerTier,
    TimeHorizon,
    OutputFormat,
    SourceType,
)
from backend.app.models.orm.insight_orm import InsightTemplateORM, InsightOutputORM


def _orm_to_template(row: InsightTemplateORM) -> InsightTemplate:
    return InsightTemplate(
        template_id=uuid.UUID(row.template_id),
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description or "",
        is_public=row.is_public,
        created_at=row.created_at,
        intent_prompt=row.intent_prompt,
        coverage_peers_summary=row.coverage_peers_summary,
        benchmark_extremes_only=row.benchmark_extremes_only,
        max_benchmark_peers=row.max_benchmark_peers,
        time_windows=[TimeHorizon(t) for t in (row.time_windows or [])],
        quant_metrics=row.quant_metrics or [],
        fragment_source_types=[SourceType(s) for s in (row.fragment_source_types or [])],
        fragment_keywords=row.fragment_keywords or [],
        max_fragments=row.max_fragments,
        output_formats=[OutputFormat(f) for f in (row.output_formats or [])],
        chart_style=row.chart_style or "AI_DECIDE",
        causation_analysis=row.causation_analysis,
        web_search_fallback=row.web_search_fallback,
        min_fragment_confidence=row.min_fragment_confidence,
        staleness_threshold_days=row.staleness_threshold_days,
    )


def _orm_to_output(row: InsightOutputORM) -> InsightOutput:
    peers = [
        PeerWithTier(
            ticker=p["ticker"],
            name=p["name"],
            tier=PeerTier(p["tier"]),
        )
        for p in (row.peer_set or [])
    ]
    return InsightOutput(
        insight_id=uuid.UUID(row.insight_id),
        template_id=uuid.UUID(row.template_id),
        tenant_id=row.tenant_id,
        status=InsightStatus(row.status),
        entities=row.entities or [],
        peer_set=peers,
        metrics=row.metrics or [],
        time_windows=row.time_windows or [],
        execution_plan=row.execution_plan,
        headline=row.headline,
        data_table=row.data_table,
        chart_specs=row.chart_specs,
        narrative=row.narrative,
        bullet_points=row.bullet_points or [],
        source_fragment_ids=row.source_fragment_ids or [],
        prior_insight_id=uuid.UUID(row.prior_insight_id) if row.prior_insight_id else None,
        confidence_score=row.confidence_score,
        source_tier_breakdown=row.source_tier_breakdown or {},
        corroboration_count=row.corroboration_count or 0,
        web_search_used=row.web_search_used or False,
        fragment_gap_warning=row.fragment_gap_warning,
        user_rating=row.user_rating,
        user_edits=row.user_edits,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


class InsightPostgresAdapter(InsightRepository):
    """
    ADAPTER: Fulfils InsightRepository using SQLAlchemy + the shared session.
    Completely independent of PostgresAdapter.
    """

    def __init__(self, session: Session):
        self.session = session

    # -----------------------------------------------------------------------
    # InsightTemplate
    # -----------------------------------------------------------------------

    def save_template(self, template: InsightTemplate) -> bool:
        try:
            existing = self.session.query(InsightTemplateORM).filter(
                InsightTemplateORM.template_id == str(template.template_id)
            ).first()

            data = dict(
                tenant_id=template.tenant_id,
                name=template.name,
                description=template.description,
                is_public=template.is_public,
                intent_prompt=template.intent_prompt,
                coverage_peers_summary=template.coverage_peers_summary,
                benchmark_extremes_only=template.benchmark_extremes_only,
                max_benchmark_peers=template.max_benchmark_peers,
                time_windows=[t for t in template.time_windows],
                quant_metrics=template.quant_metrics,
                fragment_source_types=[s for s in template.fragment_source_types],
                fragment_keywords=template.fragment_keywords,
                max_fragments=template.max_fragments,
                output_formats=[f for f in template.output_formats],
                chart_style=template.chart_style,
                causation_analysis=template.causation_analysis,
                web_search_fallback=template.web_search_fallback,
                min_fragment_confidence=template.min_fragment_confidence,
                staleness_threshold_days=template.staleness_threshold_days,
            )

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
            else:
                row = InsightTemplateORM(
                    template_id=str(template.template_id),
                    created_at=template.created_at,
                    **data,
                )
                self.session.add(row)

            self.session.commit()
            return True
        except Exception as e:
            print(f"[InsightPostgresAdapter] save_template error: {e}")
            self.session.rollback()
            return False

    def get_template(self, template_id: uuid.UUID) -> Optional[InsightTemplate]:
        row = self.session.query(InsightTemplateORM).filter(
            InsightTemplateORM.template_id == str(template_id)
        ).first()
        return _orm_to_template(row) if row else None

    def list_templates(self, tenant_id: str) -> List[InsightTemplate]:
        rows = self.session.query(InsightTemplateORM).filter(
            (InsightTemplateORM.tenant_id == tenant_id) |
            (InsightTemplateORM.is_public == True)  # noqa: E712
        ).all()
        return [_orm_to_template(r) for r in rows]

    def delete_template(self, template_id: uuid.UUID, tenant_id: str) -> bool:
        row = self.session.query(InsightTemplateORM).filter(
            InsightTemplateORM.template_id == str(template_id),
            InsightTemplateORM.tenant_id == tenant_id,
        ).first()
        if not row:
            return False
        try:
            self.session.delete(row)
            self.session.commit()
            return True
        except Exception as e:
            print(f"[InsightPostgresAdapter] delete_template error: {e}")
            self.session.rollback()
            return False

    # -----------------------------------------------------------------------
    # InsightOutput
    # -----------------------------------------------------------------------

    def save_output(self, output: InsightOutput) -> bool:
        try:
            existing = self.session.query(InsightOutputORM).filter(
                InsightOutputORM.insight_id == str(output.insight_id)
            ).first()

            peer_list = [p.model_dump() for p in output.peer_set]

            data = dict(
                template_id=str(output.template_id),
                tenant_id=output.tenant_id,
                status=output.status,
                entities=output.entities,
                peer_set=peer_list,
                metrics=output.metrics,
                time_windows=output.time_windows,
                execution_plan=output.execution_plan,
                headline=output.headline,
                data_table=output.data_table,
                chart_specs=output.chart_specs,
                narrative=output.narrative,
                bullet_points=output.bullet_points,
                source_fragment_ids=output.source_fragment_ids,
                prior_insight_id=str(output.prior_insight_id) if output.prior_insight_id else None,
                confidence_score=output.confidence_score,
                source_tier_breakdown=output.source_tier_breakdown,
                corroboration_count=output.corroboration_count,
                web_search_used=output.web_search_used,
                fragment_gap_warning=output.fragment_gap_warning,
                user_rating=output.user_rating,
                user_edits=output.user_edits,
                completed_at=output.completed_at,
            )

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
            else:
                row = InsightOutputORM(
                    insight_id=str(output.insight_id),
                    created_at=output.created_at,
                    **data,
                )
                self.session.add(row)

            self.session.commit()
            return True
        except Exception as e:
            print(f"[InsightPostgresAdapter] save_output error: {e}")
            self.session.rollback()
            return False

    def get_output(self, insight_id: uuid.UUID) -> Optional[InsightOutput]:
        row = self.session.query(InsightOutputORM).filter(
            InsightOutputORM.insight_id == str(insight_id)
        ).first()
        return _orm_to_output(row) if row else None

    def get_outputs_by_entity(
        self, tenant_id: str, entity: str, limit: int = 10
    ) -> List[InsightOutput]:
        # JSON array containment filtering — done in Python after DB fetch
        # because SQLite (dev) and Postgres (prod) handle JSON differently.
        rows = (
            self.session.query(InsightOutputORM)
            .filter(InsightOutputORM.tenant_id == tenant_id)
            .order_by(InsightOutputORM.created_at.desc())
            .limit(limit * 5)   # over-fetch to allow in-Python filtering
            .all()
        )
        results = [
            _orm_to_output(r)
            for r in rows
            if entity.upper() in [e.upper() for e in (r.entities or [])]
        ]
        return results[:limit]

    def get_outputs_by_template(
        self, tenant_id: str, template_id: uuid.UUID, limit: int = 10
    ) -> List[InsightOutput]:
        rows = (
            self.session.query(InsightOutputORM)
            .filter(
                InsightOutputORM.tenant_id == tenant_id,
                InsightOutputORM.template_id == str(template_id),
            )
            .order_by(InsightOutputORM.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_orm_to_output(r) for r in rows]

    def update_output_rating(
        self,
        insight_id: uuid.UUID,
        rating: str,
        edits: Optional[str] = None,
    ) -> bool:
        row = self.session.query(InsightOutputORM).filter(
            InsightOutputORM.insight_id == str(insight_id)
        ).first()
        if not row:
            return False
        try:
            row.user_rating = rating
            if edits is not None:
                row.user_edits = edits
            self.session.commit()
            return True
        except Exception as e:
            print(f"[InsightPostgresAdapter] update_output_rating error: {e}")
            self.session.rollback()
            return False

    def find_cached_insight(
        self,
        tenant_id: str,
        template_id: uuid.UUID,
        entities: List[str],
        staleness_days: int,
    ) -> Optional[InsightOutput]:
        cutoff = datetime.utcnow() - timedelta(days=staleness_days)
        rows = (
            self.session.query(InsightOutputORM)
            .filter(
                InsightOutputORM.tenant_id == tenant_id,
                InsightOutputORM.template_id == str(template_id),
                InsightOutputORM.status == InsightStatus.COMPLETE.value,
                InsightOutputORM.completed_at >= cutoff,
            )
            .order_by(InsightOutputORM.completed_at.desc())
            .all()
        )
        # Match on entity set (order-insensitive)
        target = {e.upper() for e in entities}
        for row in rows:
            if {e.upper() for e in (row.entities or [])} == target:
                return _orm_to_output(row)
        return None
