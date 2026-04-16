"""
Tag taxonomy for the earnings-corpus fragment tagger.

Single source of truth used by:
  - tagger.py        (to build the LLM prompt)
  - fragment_store   (for retrieval-side filtering)
  - frontend         (optional: to render tag pills)

Versioning: bump TAXONOMY_VERSION whenever tags are added, removed, or
semantically redefined. Every persisted fragment stores the tagger_version
it was tagged with so we can re-tag selectively.

Multi-tagging: every chunk gets 1-3 tags. Overlap is expected and encouraged
(e.g. "AI-driven hyperscaler demand with pricing tailwinds" gets
ai_commentary + demand_commentary + pricing_dynamics).
"""
from __future__ import annotations

TAXONOMY_VERSION = "v1"


# ---------------------------------------------------------------------------
# Tag definitions: slug -> one-sentence description used in the LLM prompt
# ---------------------------------------------------------------------------

TAGS: dict[str, str] = {
    # Forward-looking
    "outlook_guidance": (
        "Quantitative guidance for upcoming quarters or fiscal year (revenue, "
        "margin, EPS, FCF, capex targets); qualitative forward-looking commentary "
        "on trajectory and assumptions; long-term financial targets and exit rates."
    ),
    "demand_commentary": (
        "End-market demand signals, customer ordering behavior, cyclical turns, "
        "hyperscaler and enterprise appetite, consumer pull-through; also order "
        "volumes, backlog size, order coverage, book-to-bill ratios, deferred "
        "revenue, RPO (remaining performance obligations), and pipeline visibility."
    ),
    "pricing_dynamics": (
        "ASP trends, price/mix impact, pricing power commentary, price hikes or "
        "cuts, discounting pressure, cost pass-through, pricing actions by product "
        "generation."
    ),

    # Supply dynamics
    "supply_inventory": (
        "Supply situation, industry capacity, wafer starts, channel inventory "
        "levels, lead times, supplier commentary, upstream materials availability, "
        "factory input availability."
    ),
    "bottleneck_shortage": (
        "Any mention of items as bottlenecks or constraints, products in high "
        "demand that are hard to get, shortages, allocations to customers, long "
        "lead times, sharp price increases driven by tightness, 'we cannot meet "
        "demand', components or chemicals or tools in short supply."
    ),

    # Results drivers
    "revenue_drivers": (
        "What drove the reported quarter's top-line; segment, product, end-market "
        "and geography contribution to growth; one-time revenue items; unit volume "
        "drivers."
    ),
    "margin_drivers": (
        "Gross, operating, and net margin bridges; mix impact, cost inflation, "
        "operating leverage, absorption, yield improvements, depreciation step-ups, "
        "one-time charges, FX impact on margins."
    ),
    "opex_commentary": (
        "R&D intensity and R&D program focus; SG&A trends; sales and marketing "
        "efficiency; cost discipline programs; operating expense commentary. "
        "Workforce-specific language (hiring, layoffs) goes to workforce_hiring."
    ),
    "segment_performance": (
        "Business-segment results (data center, client, mobile, foundry, "
        "automotive, networking, etc.); segment-specific revenue, margin, and "
        "commentary."
    ),
    "geographic_performance": (
        "Regional revenue and margin splits; US, China, Europe, Japan, Korea, "
        "emerging markets; export controls and regional trade policy impact."
    ),

    # Capital & structure
    "capital_return": (
        "Share buybacks, dividends, dividend hikes, authorization changes, payout "
        "ratio, capital return prioritization."
    ),
    "balance_sheet_liquidity": (
        "Cash position, marketable securities, total debt, leverage ratios, debt "
        "issuance, refinancing activity, credit facilities, liquidity commentary."
    ),
    "cash_flow_commentary": (
        "Operating cash flow and free cash flow drivers, working capital dynamics "
        "(receivables, inventory, payables), cash conversion, seasonality, FCF "
        "quality."
    ),
    "m_and_a": (
        "Acquisitions announced or closed, divestitures, integration progress, "
        "deal synergies and realization, pipeline of future deals, joint ventures."
    ),

    # Capacity & operations
    "capacity": (
        "Factory and fab utilization rates, current production capacity, plans to "
        "add or reduce capacity, new site and fab announcements, project ramp-up "
        "progress and timelines, yield maturation, equipment install progress, "
        "capacity tightness and slack, capex budgets as they relate to capacity."
    ),

    # Strategic & thematic
    "investment_focus_expansion": (
        "Strategic investment priorities, business focus areas, expansion into new "
        "markets or geographies, new product lines and roadmaps as investment "
        "areas, adjacent market entries, platform expansions, partnership-driven "
        "expansion, direction of R&D dollars."
    ),
    "strategy_narrative": (
        "Long-term strategic direction, competitive positioning, business model "
        "evolution, vision statements, multi-year transformation plans."
    ),
    "ai_commentary": (
        "AI-specific language — training and inference workloads, AI "
        "infrastructure, LLMs, agentic systems, reasoning models, GenAI adoption, "
        "AI software and tooling, AI factory and hyperscaler commentary."
    ),
    "customer_commentary": (
        "Specific named customers, customer concentration changes, customer "
        "diversification, major wins and losses, design wins, customer roadmap "
        "alignment."
    ),

    # People
    "workforce_hiring": (
        "Hiring plans and pace, headcount growth or shrinkage, talent investment, "
        "workforce reductions and layoffs, talent acquisition and retention, "
        "return-to-office, people strategy commentary."
    ),

    # Risk & headwinds
    "risk_headwinds": (
        "Management-acknowledged risks and headwinds, supply chain disruptions, "
        "geopolitical impact, export controls, regulation, cyclical downturns, "
        "macro uncertainty, customer concentration risk."
    ),
    "restructuring": (
        "Restructuring actions (plant closures, impairments, restructuring "
        "charges, reorganizations, cost-cut programs). Pure layoff commentary "
        "without a restructuring framing goes to workforce_hiring."
    ),

    # Catch-all
    "other": (
        "Boilerplate cover sheet, footnotes, disclaimers, safe harbor language, "
        "signatures, date headers, contact information — chunks that aren't "
        "substantive commentary on any of the other tags."
    ),
}

TAG_SLUGS: list[str] = list(TAGS.keys())


# ---------------------------------------------------------------------------
# Co-occurrence examples used as few-shot hints for the tagger
# ---------------------------------------------------------------------------

FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "text": (
            "Data center revenue of $30.8 billion was up 73% year-over-year, driven "
            "by accelerating demand from hyperscale customers deploying our Blackwell "
            "platform for frontier-model training and inference. Supply remained tight "
            "throughout the quarter and we expect this dynamic to persist into Q1 as "
            "we ramp new capacity."
        ),
        "tags": ["revenue_drivers", "ai_commentary", "demand_commentary", "bottleneck_shortage"],
    },
    {
        "text": (
            "For fiscal Q3 2026 we are guiding revenue of $33.5 billion, plus or minus "
            "$750 million, with GAAP gross margin of approximately 81%. We expect "
            "operating expenses to grow in the low-double-digit range reflecting "
            "continued investment in data-center R&D."
        ),
        "tags": ["outlook_guidance", "opex_commentary"],
    },
    {
        "text": (
            "We are announcing today the planned closure of our Dresden facility and a "
            "workforce reduction of approximately 15,000 roles across the company, "
            "which we expect will yield annualized cost savings of $10 billion by 2027."
        ),
        "tags": ["restructuring", "workforce_hiring"],
    },
    {
        "text": (
            "Our Arizona fab project remains on track with first production wafers "
            "expected in the back half of calendar 2025. Total capex investment over "
            "the program is unchanged at approximately $40 billion."
        ),
        "tags": ["capacity", "investment_focus_expansion"],
    },
    {
        "text": (
            "In fiscal 2025 we repurchased $95 billion of our common stock and paid "
            "$15 billion in dividends, returning nearly all of our free cash flow to "
            "shareholders."
        ),
        "tags": ["capital_return", "cash_flow_commentary"],
    },
    {
        "text": (
            "Safe Harbor Statement: Statements in this press release that are not "
            "historical facts are forward-looking statements within the meaning of the "
            "Private Securities Litigation Reform Act of 1995..."
        ),
        "tags": ["other"],
    },
]


def build_prompt_taxonomy_block() -> str:
    """
    Returns a string suitable for pasting into an LLM prompt that describes
    every tag with its slug and description.
    """
    lines = []
    for slug, desc in TAGS.items():
        lines.append(f"  - {slug}: {desc}")
    return "\n".join(lines)


def build_few_shot_block() -> str:
    lines = []
    for ex in FEW_SHOT_EXAMPLES:
        tag_str = ", ".join(ex["tags"])
        lines.append(f'Text: "{ex["text"]}"\nTags: [{tag_str}]\n')
    return "\n".join(lines)
