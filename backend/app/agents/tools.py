"""
tools.py -- Provider-neutral tool definitions for the Engine agent.

TOOL_SPECS is the canonical source of truth. It uses standard JSON Schema
format (lowercase types) and is imported by every LLM adapter, which converts
it to the provider's native tool format before the API call.

Adding a new data source
------------------------
  Qualitative (documents):
    1. Add a new extractor -> Pinecone with a new doc_type tag.
    2. Update the doc_types description in the search_documents spec below.
    Zero other changes.

  Quantitative (structured data):
    1. Add a new entry to TOOL_SPECS.
    2. Add a new executor in agents/executors/.
    Zero changes to EngineAgent or existing tools.

Provider conversion
-------------------
  Each adapter calls the appropriate converter in this module:
    to_anthropic_tools(TOOL_SPECS) -> Anthropic native format
    to_openai_tools(TOOL_SPECS)    -> OpenAI native format
    to_gemini_tools(TOOL_SPECS)    -> Gemini native format

  When a new provider is added: add one converter function here.
"""
from __future__ import annotations

from backend.app.services.data_agent.concept_map import ALL_METRICS

# Auto-generated from concept_map -- always in sync with what DataAgent supports
_METRIC_LIST = ", ".join(sorted(ALL_METRICS))

# ---------------------------------------------------------------------------
# Canonical tool definitions (provider-neutral JSON Schema format)
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict] = [
    {
        "name": "get_financial_data",
        "description": (
            "Retrieve quarterly financial metrics for one or more public companies. "
            "Use this for quantitative questions: revenue, profitability, margins, "
            "EPS, cash flow, balance sheet items, and YoY/QoQ growth rates. "
            "Returns a table of quarterly data per ticker. "
            f"Available metrics: {_METRIC_LIST}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ticker symbols, e.g. [\"NVDA\", \"AMD\"]. Must be uppercase.",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Exact metric names from the available list. "
                        "Use gross_margin_pct (not gross_margin), "
                        "revenue_yoy_pct for YoY growth."
                    ),
                },
                "periods": {
                    "type": "integer",
                    "description": "Number of recent quarters to return. Default 8, max 20.",
                },
            },
            "required": ["tickers", "metrics"],
        },
    },
    {
        "name": "search_documents",
        "description": (
            "Semantic search over indexed financial documents. Use this for qualitative "
            "questions about strategy, management commentary, competitive dynamics, "
            "risks, analyst views, or any question requiring text evidence from filings "
            "or transcripts. "
            "Available document types: 10-K, 10-Q, 8-K, transcript, broker_report, "
            "note, news."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Semantic search query in natural language.",
                },
                "ticker_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict results to these ticker symbols (optional).",
                },
                "doc_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by document type (optional). "
                        "Options: 10-K, 10-Q, 8-K, transcript, broker_report, note, news."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10).",
                },
            },
            "required": ["query"],
        },
    },
]


# ---------------------------------------------------------------------------
# Provider-specific converters
# Called inside each adapter's generate_with_tools() method.
# ---------------------------------------------------------------------------

def to_anthropic_tools(specs: list[dict]) -> list[dict]:
    """
    Neutral spec -> Anthropic tool-use format.
    { name, description, input_schema }
    """
    return [
        {
            "name":         spec["name"],
            "description":  spec["description"],
            "input_schema": spec["parameters"],
        }
        for spec in specs
    ]


def to_openai_tools(specs: list[dict]) -> list[dict]:
    """
    Neutral spec -> OpenAI function-calling format.
    [{ type: "function", function: { name, description, parameters } }]
    """
    return [
        {
            "type": "function",
            "function": {
                "name":        spec["name"],
                "description": spec["description"],
                "parameters":  spec["parameters"],
            },
        }
        for spec in specs
    ]


def to_gemini_tools(specs: list[dict]) -> list[dict]:
    """
    Neutral spec -> Gemini function_declarations format.
    Gemini requires uppercase type names (OBJECT, STRING, ARRAY, INTEGER, NUMBER).
    [{ function_declarations: [{ name, description, parameters }] }]
    """
    return [
        {
            "function_declarations": [
                {
                    "name":        spec["name"],
                    "description": spec["description"],
                    "parameters":  _upcase_types(spec["parameters"]),
                }
                for spec in specs
            ]
        }
    ]


def _upcase_types(schema: dict) -> dict:
    """
    Recursively convert JSON Schema lowercase type strings to Gemini uppercase.
    e.g. "object" -> "OBJECT", "string" -> "STRING", "array" -> "ARRAY"
    """
    _MAP = {
        "object":  "OBJECT",
        "array":   "ARRAY",
        "string":  "STRING",
        "integer": "INTEGER",
        "number":  "NUMBER",
        "boolean": "BOOLEAN",
    }
    result = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            result[k] = _MAP.get(v, v.upper())
        elif k == "properties" and isinstance(v, dict):
            result[k] = {pk: _upcase_types(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            result[k] = _upcase_types(v)
        else:
            result[k] = v
    return result
