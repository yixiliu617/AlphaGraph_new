"""
Phase 9 — Insight Persistence.

Saves the completed InsightOutput to the insight repository and
updates its status to COMPLETE.

Replacing or skipping this step: remove the call in runner.py.
No other file is affected.
"""

from datetime import datetime

from backend.app.interfaces.insight_repository import InsightRepository
from backend.app.models.domain.insight_models import InsightOutput, InsightStatus


def persist_insight(
    output: InsightOutput,
    insight_repo: InsightRepository,
) -> InsightOutput:
    """
    Phase 9: Mark the insight as COMPLETE, set completed_at, and save.

    Returns the updated InsightOutput so the runner can return it to the caller.
    """
    output.status = InsightStatus.COMPLETE
    output.completed_at = datetime.utcnow()

    saved = insight_repo.save_output(output)
    if not saved:
        print(f"[persist_insight] Warning: failed to persist insight {output.insight_id}.")

    return output
