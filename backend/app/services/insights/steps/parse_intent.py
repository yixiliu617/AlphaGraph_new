"""
Phase 0 — Intent Parsing.

Takes a natural language query + a chosen InsightTemplate and resolves:
  - Which entities to analyse
  - Which metrics to use (from template or AI-decided)
  - Which time windows apply
  - Preliminary peer count estimate

Returns an (InsightOutput in PENDING state, ExecutionPlan) tuple.
The ExecutionPlan is shown to the user for confirmation before any
expensive data fetching begins.

Replacing or skipping this step: swap parse_intent() in runner.py.
No other file is affected.
"""

from typing import Tuple, List

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.models.domain.insight_models import (
    InsightTemplate,
    InsightOutput,
    InsightStatus,
    ExecutionPlan,
    PeerWithTier,
    PeerTier,
    TimeHorizon,
)


# JSON Schema for LLM structured output
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ticker symbols extracted from the query, e.g. ['INTC']",
        },
        "metrics": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Financial metric names. Use the template's quant_metrics if provided; "
                "otherwise infer from the query."
            ),
        },
        "time_windows": {
            "type": "array",
            "items": {"type": "string", "enum": ["1Y", "5Y", "10Y"]},
            "description": "Time horizons inferred from the query, or the template defaults.",
        },
    },
    "required": ["entities", "metrics", "time_windows"],
}


def parse_intent(
    user_query: str,
    template: InsightTemplate,
    llm: LLMProvider,
) -> Tuple[InsightOutput, ExecutionPlan]:
    """
    Phase 0: parse the user's query against the template to produce a
    structured ExecutionPlan and an InsightOutput in PENDING state.

    The peer_set is left empty here — Phase 2 (resolve_peers) fills it in.
    The ExecutionPlan returned here is a preview; peer details are added
    after Phase 2 and the combined plan is stored on the InsightOutput.
    """
    prompt = (
        f"The user asked: \"{user_query}\"\n\n"
        f"The selected insight template is: \"{template.name}\".\n"
        f"Template intent: {template.intent_prompt}\n\n"
        f"Template default metrics: {template.quant_metrics or 'not specified — you must infer'}\n"
        f"Template default time windows: {[t for t in template.time_windows]}\n\n"
        "Extract the entities (ticker symbols), metrics, and time windows "
        "from the user's query. If the user did not specify metrics, use "
        "the template defaults. If the user did not specify time windows, "
        "use the template defaults."
    )

    try:
        parsed = llm.generate_structured_output(
            prompt=prompt,
            output_schema=_INTENT_SCHEMA,
        )
    except Exception as e:
        # Graceful fallback: use template defaults + no entities
        print(f"[parse_intent] LLM call failed ({e}), using template defaults.")
        parsed = {
            "entities": [],
            "metrics": template.quant_metrics,
            "time_windows": [t for t in template.time_windows],
        }

    entities: List[str] = [e.upper() for e in (parsed.get("entities") or [])]
    metrics: List[str] = parsed.get("metrics") or template.quant_metrics
    raw_windows: List[str] = parsed.get("time_windows") or [t for t in template.time_windows]

    # Normalise to TimeHorizon values (ignore anything unrecognised)
    valid_values = {h.value for h in TimeHorizon}
    time_windows = [w for w in raw_windows if w in valid_values]
    if not time_windows:
        time_windows = [TimeHorizon.SHORT_1Y.value, TimeHorizon.MID_5Y.value, TimeHorizon.LONG_10Y.value]

    # Build a preliminary ExecutionPlan (peers added by resolve_peers later)
    plan = ExecutionPlan(
        entities=entities,
        peers=[],  # filled in Phase 2
        metrics=metrics,
        time_windows=time_windows,
        source_types=[s for s in template.fragment_source_types] or ["all"],
        output_formats=[f for f in template.output_formats],
        causation_analysis=template.causation_analysis,
        web_search_allowed=template.web_search_fallback,
        expected_fragment_count=0,  # estimated in Phase 4b
    )

    output = InsightOutput(
        template_id=template.template_id,
        tenant_id="",  # set by the runner after receiving tenant_id
        status=InsightStatus.PENDING,
        entities=entities,
        peer_set=[],
        metrics=metrics,
        time_windows=time_windows,
        execution_plan=plan.model_dump(),
    )

    return output, plan
