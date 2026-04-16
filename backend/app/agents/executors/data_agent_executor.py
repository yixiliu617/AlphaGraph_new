"""
data_agent_executor.py -- QueryExecutor for the get_financial_data tool call.

Routes to DataAgent which handles topline -> calculated layer routing internally.
Returns (AgentResponseBlock, summary_for_llm).

Result bifurcation:
  block.data  -> full rows/periods/metrics -> frontend AgentBlockRenderer
  summary     -> compact ~50-token string  -> Claude synthesis pass only

This keeps Claude's context lean: full financial tables never flow back
through the LLM, only a brief stats summary.
"""
from __future__ import annotations

import math

from backend.app.agents.executors.base import QueryExecutor
from backend.app.models.api_contracts import AgentResponseBlock
from backend.app.services.data_agent.data_agent import DataAgent, DataSpec
from backend.app.services.data_agent.concept_map import METRIC_META


class DataAgentExecutor(QueryExecutor):
    """Handles get_financial_data tool calls via DataAgent."""

    def can_handle(self, tool_call: dict) -> bool:
        return tool_call.get("name") == "get_financial_data"

    def execute(self, tool_call: dict) -> tuple:
        inp      = tool_call.get("input", {})
        tickers  = [t.upper() for t in inp.get("tickers", [])]
        metrics  = inp.get("metrics", [])
        periods  = min(int(inp.get("periods", 8)), 20)

        if not tickers or not metrics:
            block = AgentResponseBlock(
                block_type="financial_table",
                title="Financial Data",
                data={"rows": [], "warnings": ["No tickers or metrics specified."]},
            )
            return block, "No financial data: missing tickers or metrics."

        # Convert quarters -> years with a small buffer
        lookback_years = max(2.0, round(periods / 4 + 0.5, 1))

        spec   = DataSpec(tickers=tickers, metrics=metrics, lookback_years=lookback_years)
        result = DataAgent().fetch(spec)

        # Trim to requested number of periods per ticker (most recent first)
        rows = _trim_rows(result.rows, result.tickers, periods)

        block = AgentResponseBlock(
            block_type="financial_table",
            title=_block_title(tickers, metrics),
            data={
                "rows":     rows,
                "tickers":  result.tickers,
                "periods":  result.periods,
                "metrics":  result.metrics_returned,
                "source":   result.source,
                "warnings": result.warnings,
            },
        )
        summary = _summarize_for_llm(tickers, metrics, rows, result)
        return block, summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trim_rows(rows: list[dict], tickers: list[str], n: int) -> list[dict]:
    """Keep the most recent n quarters per ticker."""
    out: list[dict] = []
    for ticker in tickers:
        ticker_rows = [r for r in rows if r.get("ticker") == ticker]
        out.extend(ticker_rows[-n:])
    return out


def _block_title(tickers: list[str], metrics: list[str]) -> str:
    ticker_str = " / ".join(tickers) if tickers else "Unknown"
    labels = [METRIC_META.get(m, {}).get("label", m) for m in metrics[:3]]
    label_str = ", ".join(labels)
    if len(metrics) > 3:
        label_str += f" +{len(metrics) - 3} more"
    return f"{ticker_str} -- {label_str}"


def _summarize_for_llm(
    tickers: list[str],
    metrics: list[str],
    rows: list[dict],
    result,
) -> str:
    """
    Compact text summary for Claude's synthesis pass.
    Full data goes to the frontend; Claude only needs key stats.
    Capped at ~80 tokens to keep context lean.
    """
    if not rows:
        warn_str = "; ".join(result.warnings) if result.warnings else "unknown reason"
        return f"No financial data found for {', '.join(tickers)}. {warn_str}"

    lines = [f"Financial data for {', '.join(result.tickers)}."]

    # Show the last 4 period labels only
    recent_periods = result.periods[-4:] if result.periods else []
    if recent_periods:
        lines.append(f"Recent periods: {', '.join(recent_periods)}.")

    # Stats for up to 4 metrics on the first ticker
    primary = result.tickers[0] if result.tickers else tickers[0]
    for metric in metrics[:4]:
        values = [
            r[metric] for r in rows
            if r.get("ticker") == primary
            and r.get(metric) is not None
            and not (isinstance(r.get(metric), float) and math.isnan(r[metric]))
        ]
        if not values:
            continue
        label = METRIC_META.get(metric, {}).get("label", metric)
        unit  = METRIC_META.get(metric, {}).get("unit", "")
        try:
            lo, hi = min(values), max(values)
            latest = values[-1]
            lines.append(
                f"{label}: latest {latest:.1f}{unit}, range {lo:.1f}-{hi:.1f}{unit}."
            )
        except (TypeError, ValueError):
            pass

    if result.warnings:
        lines.append(f"Note: {'; '.join(result.warnings[:2])}")

    return " ".join(lines)
