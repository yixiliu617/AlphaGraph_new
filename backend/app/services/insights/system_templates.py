"""
System-provided InsightTemplates.

These are served as static constants — no DB required, never stale.
When a user wants to customise one, the API creates a private copy in their tenant.

To add a new system template: add an InsightTemplate entry to SYSTEM_TEMPLATES.
No other file needs to change.
"""

import uuid
from backend.app.models.domain.insight_models import (
    InsightTemplate,
    TimeHorizon,
    OutputFormat,
    SourceType,
)

# Stable IDs so the frontend can reference them without querying the DB.
_IDS = {
    "margin_trend":     uuid.UUID("00000001-0000-0000-0000-000000000001"),
    "revenue_quality":  uuid.UUID("00000001-0000-0000-0000-000000000002"),
    "sector_rollup":    uuid.UUID("00000001-0000-0000-0000-000000000003"),
    "catalyst_tracker": uuid.UUID("00000001-0000-0000-0000-000000000004"),
    "supply_chain":     uuid.UUID("00000001-0000-0000-0000-000000000005"),
    "earnings_quality": uuid.UUID("00000001-0000-0000-0000-000000000006"),
}

SYSTEM_TEMPLATES: list[InsightTemplate] = [

    InsightTemplate(
        template_id=_IDS["margin_trend"],
        tenant_id="system",
        name="Margin Trend Analysis",
        description=(
            "Analyse gross margin, operating margin, and net margin for a company "
            "vs its GICS subindustry peers over 1, 5, and 10 years. "
            "Includes causation pass to explain drivers."
        ),
        is_public=True,
        intent_prompt=(
            "Analyse {entity}'s gross margin, operating margin, and net margin "
            "expansion or contraction vs its sector peers over {time_windows}. "
            "Identify the key drivers behind any significant moves."
        ),
        quant_metrics=["gross_margin", "operating_margin", "net_margin"],
        fragment_keywords=["margin", "cost of goods", "operating expenses", "profitability"],
        fragment_source_types=[SourceType.SEC_FILING, SourceType.BROKER_REPORT, SourceType.TRANSCRIPT],
        time_windows=[TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y, TimeHorizon.LONG_10Y],
        output_formats=[OutputFormat.DATA_TABLE, OutputFormat.CHART, OutputFormat.TEXT_SUMMARY, OutputFormat.BULLETS],
        chart_style="line",
        causation_analysis=True,
    ),

    InsightTemplate(
        template_id=_IDS["revenue_quality"],
        tenant_id="system",
        name="Revenue Quality Analysis",
        description=(
            "Assess whether revenue growth is real and durable: organic vs inorganic, "
            "beat/miss history, and concentration risk."
        ),
        is_public=True,
        intent_prompt=(
            "Assess {entity}'s revenue quality and durability vs peers over {time_windows}. "
            "Focus on organic growth, beat/miss history, and customer concentration."
        ),
        quant_metrics=["revenue", "revenue_growth_yoy", "organic_revenue_growth"],
        fragment_keywords=["revenue", "organic growth", "customer concentration", "guidance"],
        fragment_source_types=[SourceType.SEC_FILING, SourceType.TRANSCRIPT, SourceType.BROKER_REPORT],
        time_windows=[TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y, TimeHorizon.LONG_10Y],
        output_formats=[OutputFormat.DATA_TABLE, OutputFormat.CHART, OutputFormat.TEXT_SUMMARY, OutputFormat.BULLETS],
        chart_style="bar",
        causation_analysis=True,
    ),

    InsightTemplate(
        template_id=_IDS["sector_rollup"],
        tenant_id="system",
        name="Sector Roll-Up Snapshot",
        description=(
            "Bottom-up sector view: aggregate key metrics across all companies in "
            "the user's coverage for a chosen sector. Great for earnings-season context."
        ),
        is_public=True,
        intent_prompt=(
            "Provide a sector-level roll-up of key financial metrics "
            "across all companies in {entity}'s GICS sector over {time_windows}."
        ),
        quant_metrics=["revenue_growth_yoy", "gross_margin", "operating_margin", "net_margin"],
        fragment_keywords=["sector", "industry", "peers", "competitive landscape"],
        fragment_source_types=[SourceType.BROKER_REPORT, SourceType.TRANSCRIPT],
        time_windows=[TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y],
        output_formats=[OutputFormat.DATA_TABLE, OutputFormat.CHART, OutputFormat.BULLETS],
        chart_style="bar",
        causation_analysis=False,
        coverage_peers_summary=True,
        benchmark_extremes_only=True,
    ),

    InsightTemplate(
        template_id=_IDS["catalyst_tracker"],
        tenant_id="system",
        name="Catalyst Tracker",
        description=(
            "Link recent text fragments to active thesis catalysts. "
            "Shows which catalysts are confirmed, pending, or broken by new evidence."
        ),
        is_public=True,
        intent_prompt=(
            "Review recent evidence for or against the active investment thesis catalysts "
            "for {entity}. Classify each catalyst as confirmed, pending, or broken."
        ),
        quant_metrics=[],  # AI decides based on catalyst definitions
        fragment_keywords=["catalyst", "earnings", "guidance", "supply chain", "regulation"],
        fragment_source_types=[
            SourceType.SEC_FILING, SourceType.TRANSCRIPT, SourceType.NEWS, SourceType.BROKER_REPORT
        ],
        time_windows=[TimeHorizon.SHORT_1Y],
        output_formats=[OutputFormat.TEXT_SUMMARY, OutputFormat.BULLETS],
        chart_style="AI_DECIDE",
        causation_analysis=True,
        staleness_threshold_days=1,  # catalyst status changes fast
    ),

    InsightTemplate(
        template_id=_IDS["supply_chain"],
        tenant_id="system",
        name="Supply Chain Stress Test",
        description=(
            "Traverse the Neo4j relationship graph to identify supply chain pressure points. "
            "Finds which suppliers, customers, or partners are showing stress signals."
        ),
        is_public=True,
        intent_prompt=(
            "Identify supply chain pressure points for {entity} by analysing "
            "its graph relationships and recent fragment evidence from suppliers, "
            "customers, and competitors."
        ),
        quant_metrics=["inventory_days", "gross_margin", "capex"],
        fragment_keywords=["supply chain", "inventory", "capacity", "shortage", "lead time"],
        fragment_source_types=[SourceType.SEC_FILING, SourceType.TRANSCRIPT, SourceType.NEWS],
        time_windows=[TimeHorizon.SHORT_1Y, TimeHorizon.MID_5Y],
        output_formats=[OutputFormat.TEXT_SUMMARY, OutputFormat.BULLETS, OutputFormat.DATA_TABLE],
        chart_style="AI_DECIDE",
        causation_analysis=True,
        max_benchmark_peers=50,  # wider graph traversal for supply chain
    ),

    InsightTemplate(
        template_id=_IDS["earnings_quality"],
        tenant_id="system",
        name="Earnings Quality & Beat History",
        description=(
            "Assess earnings reliability: EPS beat/miss history, guidance accuracy, "
            "and accruals quality vs peers over a 5-10 year window."
        ),
        is_public=True,
        intent_prompt=(
            "Assess {entity}'s earnings quality and beat/miss history vs peers "
            "over {time_windows}. Include guidance accuracy and accruals analysis."
        ),
        quant_metrics=["eps_actual", "eps_consensus", "eps_beat_miss", "operating_cash_flow", "net_income"],
        fragment_keywords=["earnings", "EPS", "guidance", "beat", "miss", "accruals"],
        fragment_source_types=[SourceType.SEC_FILING, SourceType.TRANSCRIPT, SourceType.BROKER_REPORT],
        time_windows=[TimeHorizon.MID_5Y, TimeHorizon.LONG_10Y],
        output_formats=[OutputFormat.DATA_TABLE, OutputFormat.CHART, OutputFormat.TEXT_SUMMARY, OutputFormat.BULLETS],
        chart_style="bar",
        causation_analysis=False,
    ),

]

# Lookup by id for O(1) access in the router.
SYSTEM_TEMPLATES_BY_ID: dict[uuid.UUID, InsightTemplate] = {
    t.template_id: t for t in SYSTEM_TEMPLATES
}
