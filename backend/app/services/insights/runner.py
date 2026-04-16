"""
InsightRunner — orchestrates the 10-phase insight pipeline.

Each phase is a pure function in its own step file. The runner is thin:
it calls steps in sequence, passes typed inputs, collects typed outputs.

Phase A (implemented):   0, 1, 2, 3, 9
Phase B (stubbed):       4, 5, 6, 7, 8  — return empty data with a clear label

To replace any phase: edit its step file. The runner and all other steps
are unaffected. To skip a phase: remove its call here.

Dependencies injected at construction time — none are imported at module level
from other service layers, so this file has zero coupling to ExtractionRunner
or any other service.
"""

from typing import Optional, Tuple
import uuid

from backend.app.interfaces.insight_repository import InsightRepository
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.models.domain.insight_models import (
    InsightOutput,
    InsightStatus,
    ExecutionPlan,
    PeerWithTier,
)

from backend.app.services.insights.steps.parse_intent    import parse_intent
from backend.app.services.insights.steps.check_cache    import check_cache
from backend.app.services.insights.steps.resolve_peers  import resolve_peers
from backend.app.services.insights.steps.define_windows import define_windows
from backend.app.services.insights.steps.persist_insight import persist_insight


class InsightRunner:
    """
    Orchestrator for the modular insight pipeline.

    Constructor args:
      insight_repo  — InsightRepository for template + output persistence
      db_repo       — DBRepository for GICS / universe lookups (read-only usage)
      llm           — LLMProvider for intent parsing and synthesis
    """

    def __init__(
        self,
        insight_repo: InsightRepository,
        db_repo: DBRepository,
        llm: LLMProvider,
    ):
        self.insight_repo = insight_repo
        self.db_repo      = db_repo
        self.llm          = llm

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def build_plan(
        self,
        user_query: str,
        template_id: uuid.UUID,
        tenant_id: str,
    ) -> Tuple[InsightOutput, ExecutionPlan]:
        """
        Phase 0 only — parse intent and return an execution plan for the user
        to review. Does NOT run data retrieval or synthesis.

        The returned InsightOutput is saved in PENDING state so the frontend
        can reference it by insight_id when the user clicks [Run Insight ▶].
        """
        from backend.app.services.insights.system_templates import SYSTEM_TEMPLATES_BY_ID

        # Resolve template (system first, then DB).
        template = SYSTEM_TEMPLATES_BY_ID.get(template_id)
        if template is None:
            template = self.insight_repo.get_template(template_id)
        if template is None:
            raise ValueError(f"InsightTemplate {template_id} not found.")

        # Phase 0: parse intent.
        output, plan = parse_intent(user_query, template, self.llm)
        output.tenant_id = tenant_id

        # Phase 1: cache check (include in plan preview so UI can show "cache available").
        cached = check_cache(tenant_id, template, output.entities, self.insight_repo)
        if cached:
            plan_dict = plan.model_dump()
            plan_dict["cache_available"] = True
            plan_dict["cached_insight_id"] = str(cached.insight_id)
            plan_dict["cached_at"] = cached.completed_at.isoformat() if cached.completed_at else None
            output.execution_plan = plan_dict
            output.prior_insight_id = cached.insight_id
        else:
            plan_dict = plan.model_dump()
            plan_dict["cache_available"] = False
            output.execution_plan = plan_dict

        # Phase 2: resolve peers (needed for the plan preview).
        peers = resolve_peers(output.entities, tenant_id, template, self.db_repo)
        output.peer_set = peers
        output.execution_plan["peers"] = [p.model_dump() for p in peers]

        # Phase 3: normalise time windows.
        output.time_windows = define_windows(output.time_windows)
        output.execution_plan["time_windows"] = output.time_windows

        # Persist PENDING output so the frontend can reference it by id.
        self.insight_repo.save_output(output)

        return output, ExecutionPlan(**{
            k: v for k, v in output.execution_plan.items()
            if k in ExecutionPlan.model_fields
        })

    def run(
        self,
        insight_id: uuid.UUID,
        tenant_id: str,
        force_refresh: bool = False,
    ) -> InsightOutput:
        """
        Run the full pipeline for an InsightOutput that is already in PENDING state.

        If force_refresh=False and a cache hit was recorded in build_plan(),
        the cached insight is returned immediately.

        Phases 4–8 are stubbed in Phase A — they return empty data with a
        clear label so the frontend can render gracefully while Phase B is built.
        """
        output = self.insight_repo.get_output(insight_id)
        if not output:
            raise ValueError(f"InsightOutput {insight_id} not found.")
        if output.tenant_id != tenant_id:
            raise PermissionError("Insight does not belong to this tenant.")

        # Return cached insight if available and not force-refreshing.
        plan_dict = output.execution_plan or {}
        if not force_refresh and plan_dict.get("cache_available") and output.prior_insight_id:
            cached = self.insight_repo.get_output(output.prior_insight_id)
            if cached:
                return cached

        # Mark as running.
        output.status = InsightStatus.RUNNING
        self.insight_repo.save_output(output)

        try:
            # ---------------------------------------------------------------
            # Phase 4 — STUB: parallel data retrieval
            # (DuckDB quant + Pinecone fragments + Neo4j graph)
            # Will be implemented in Phase B.
            # ---------------------------------------------------------------
            quant_data    = {}       # Phase 4a stub
            raw_fragments = []       # Phase 4b stub
            graph_context = {}       # Phase 4c stub

            # ---------------------------------------------------------------
            # Phase 5 — STUB: statistical analysis
            # ---------------------------------------------------------------
            analysis_result = {}     # Phase 5 stub

            # ---------------------------------------------------------------
            # Phase 6 — STUB: parallel output prep
            # (chart specs, data table, narrative draft)
            # ---------------------------------------------------------------
            chart_specs  = []        # Phase 6a stub
            data_table   = {}        # Phase 6b stub
            narrative    = ""        # Phase 6c stub

            # ---------------------------------------------------------------
            # Phase 7 — STUB: synthesis
            # ---------------------------------------------------------------
            output.headline       = f"[Phase B pending] {', '.join(output.entities)} — {', '.join(output.metrics or [])}"
            output.narrative      = (
                "Data retrieval and synthesis (Phases 4–8) will be implemented in Phase B. "
                "The pipeline skeleton, template system, peer resolution, cache, and "
                "persistence layers are fully operational."
            )
            output.bullet_points  = [
                "Pipeline scaffold complete — Phases 0–3 and Phase 9 are live.",
                "Peer resolution ran: see execution_plan.peers for the resolved set.",
                "Time windows validated and sorted.",
                "Cache check completed — see execution_plan.cache_available.",
                "Phase B will add: DuckDB quant retrieval, Pinecone fragment search, "
                "Neo4j graph traversal, chart generation, and LLM synthesis.",
            ]
            output.chart_specs    = chart_specs
            output.data_table     = data_table or None
            output.confidence_score = None   # Phase 7d stub
            output.fragment_gap_warning = (
                "Phases 4–8 (data retrieval + synthesis) are not yet implemented. "
                "No real data has been fetched for this insight."
            )

            # ---------------------------------------------------------------
            # Phase 8 — STUB: meta-output ("what's missing", related insights)
            # ---------------------------------------------------------------

            # ---------------------------------------------------------------
            # Phase 9 — persist.
            # ---------------------------------------------------------------
            output = persist_insight(output, self.insight_repo)

        except Exception as e:
            output.status = InsightStatus.FAILED
            output.narrative = f"Pipeline error: {e}"
            self.insight_repo.save_output(output)
            raise

        return output
