"""
router_agent.py -- EngineAgent: Claude tool-use loop for the Unified Data Engine.

Architecture (tool-use pattern):
  1. Claude receives the user query + TOOLS definitions.
  2. Claude returns tool call(s) with typed parameters -- no SQL generated.
  3. ExecutorRegistry dispatches each tool call to the matching executor.
  4. Executors return (AgentResponseBlock, summary):
       - full block  -> ChatResponse.blocks -> frontend AgentBlockRenderer
       - summary     -> back to Claude (compact, ~50 tokens, NOT full data)
  5. Claude produces a 2-3 sentence synthesis using only the summaries.

Token efficiency:
  - Full financial tables never flow back through the LLM context.
  - Tool definitions are a fixed ~500-token overhead per request.
  - Synthesis pass uses summaries only, keeping context lean.

Adding a new data source:
  - Qualitative (docs): add extractor -> Pinecone, update doc_types in tools.py
  - Quantitative:       add new tool in tools.py + new executor in executors/
  Zero changes to EngineAgent.
"""
from __future__ import annotations

import uuid
from typing import Optional

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.agents.executors.executor_registry import ExecutorRegistry
from backend.app.agents.tools import TOOL_SPECS
from backend.app.models.api_contracts import ChatResponse, AgentResponseBlock

ENGINE_SYSTEM_PROMPT = """\
You are AlphaGraph, an AI research assistant for institutional portfolio managers.

You have access to two tools:
- get_financial_data: retrieve quarterly financial metrics for any public company
- search_documents:   semantic search over SEC filings, earnings transcripts,
                      broker reports, analyst notes, and company news

When answering a question:
1. Call the appropriate tool(s) with precise, specific parameters.
2. After data is retrieved, write a concise 2-3 sentence investment-focused synthesis.

Rules:
- Use exact ticker symbols (uppercase) and exact metric names.
- For comparisons, include both tickers in a single get_financial_data call.
- For mixed quant+qual questions, call both tools.
- If data is unavailable, say so clearly -- do not fabricate numbers.
"""

_SYNTHESIS_PROMPT_TEMPLATE = (
    "Data retrieved:\n{summaries}\n\n"
    "Write a concise 2-3 sentence investment-focused synthesis of the above data. "
    "Focus on the key takeaway for a portfolio manager."
)


class EngineAgent:
    """
    Tool-use agent for the Unified Data Engine.

    Single responsibility: orchestrate the Claude tool-use loop and
    assemble a ChatResponse (synthesis text + data blocks).
    """

    def __init__(self, llm: LLMProvider, registry: ExecutorRegistry):
        self.llm      = llm
        self.registry = registry

    def process_query(
        self,
        message: str,
        session_id: Optional[str] = None,
    ) -> ChatResponse:
        sid = session_id or str(uuid.uuid4())

        # ------------------------------------------------------------------
        # Pass 1: Claude decides which tools to call
        # ------------------------------------------------------------------
        text, tool_calls = self.llm.generate_with_tools(
            system=ENGINE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message}],
            tools=TOOL_SPECS,
        )

        # ------------------------------------------------------------------
        # Execute tool calls via ExecutorRegistry
        # ------------------------------------------------------------------
        blocks: list[AgentResponseBlock] = []
        summaries: list[str] = []

        if tool_calls:
            blocks, summaries = self.registry.run_all(tool_calls)

        # ------------------------------------------------------------------
        # Pass 2: Claude synthesises using compact summaries only
        # Full data tables never flow back through the LLM (token efficiency).
        # ------------------------------------------------------------------
        if summaries:
            summary_str = "\n".join(f"- {s}" for s in summaries)
            synthesis_prompt = _SYNTHESIS_PROMPT_TEMPLATE.format(summaries=summary_str)

            synthesis_text, _ = self.llm.generate_with_tools(
                system=ENGINE_SYSTEM_PROMPT,
                messages=[
                    {"role": "user",      "content": message},
                    {"role": "assistant", "content": text or "(retrieving data)"},
                    {"role": "user",      "content": synthesis_prompt},
                ],
                tools=[],  # synthesis pass: no tools, just text generation
            )
            answer = synthesis_text or text
        else:
            # No tool calls: Claude answered directly (e.g. meta questions,
            # greetings, queries outside the data domain)
            answer = text or "I could not process your query. Please try rephrasing."

        return ChatResponse(
            answer=answer,
            blocks=blocks,
            session_id=sid,
        )
