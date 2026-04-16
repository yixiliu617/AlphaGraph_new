"""
Phase 1 — Cache Check.

Before any expensive data fetching, look for a recent InsightOutput for
the same tenant + template + entity set. If one exists within the
staleness window, return it so the runner can offer a delta update
instead of a full recompute.

Replacing or skipping this step: remove the call in runner.py.
No other file is affected.
"""

from typing import Optional

from backend.app.interfaces.insight_repository import InsightRepository
from backend.app.models.domain.insight_models import InsightOutput, InsightTemplate


def check_cache(
    tenant_id: str,
    template: InsightTemplate,
    entities: list[str],
    insight_repo: InsightRepository,
) -> Optional[InsightOutput]:
    """
    Phase 1: Return a fresh cached InsightOutput, or None if no valid cache exists.

    A cache hit means the same tenant ran the same template against the
    same entity set within template.staleness_threshold_days.
    """
    if not entities:
        return None

    cached = insight_repo.find_cached_insight(
        tenant_id=tenant_id,
        template_id=template.template_id,
        entities=entities,
        staleness_days=template.staleness_threshold_days,
    )

    if cached:
        print(
            f"[check_cache] Cache HIT for tenant={tenant_id}, "
            f"template={template.name}, entities={entities}. "
            f"Insight completed at {cached.completed_at}."
        )
    else:
        print(
            f"[check_cache] Cache MISS for tenant={tenant_id}, "
            f"template={template.name}, entities={entities}. Running fresh."
        )

    return cached
