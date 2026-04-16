"""
anthropic_adapter.py -- Claude LLM adapter (Anthropic API).

Primary capability: generate_with_tools() for the Engine agent.
Accepts neutral TOOL_SPECS from tools.py and converts to Anthropic format.

Embeddings: NOT supported. Use GeminiAdapter for Pinecone embeddings.

To activate for the Engine:
    Set ENGINE_LLM=anthropic in .env
    Set ANTHROPIC_API_KEY=sk-ant-... in .env
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Union

import anthropic

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.agents.tools import to_anthropic_tools

_DEFAULT_MODEL      = "claude-sonnet-4-6"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter(LLMProvider):

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model  = model
        print(f"Anthropic Adapter initialized with model: {model}")

    # ------------------------------------------------------------------
    # Tool-use (Engine agent) -- primary method
    # ------------------------------------------------------------------

    def generate_with_tools(
        self,
        system: str,
        messages: List[Dict],
        tools: List[Dict],
    ) -> tuple:
        """
        One Claude turn with optional tool-use.
        tools should be TOOL_SPECS (neutral format) -- converted here.
        Returns (text: str, tool_calls: list[{name, input}]).
        """
        kwargs: dict = {
            "model":      self._model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "system":     system,
            "messages":   messages,  # Anthropic format: {role, content}
        }
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)

        response = self._client.messages.create(**kwargs)

        text: str = ""
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input})

        return text, tool_calls

    # ------------------------------------------------------------------
    # Standard LLMProvider methods
    # ------------------------------------------------------------------

    def generate_response(
        self,
        prompt: str,
        system_message: str = "You are a senior financial analyst.",
    ) -> str:
        text, _ = self.generate_with_tools(
            system=system_message,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
        )
        return text

    def generate_structured_output(
        self,
        prompt: str,
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        schema_str = json.dumps(output_schema, indent=2)
        system = (
            "Return a JSON object that exactly matches the following JSON Schema. "
            "Respond with raw JSON only -- no markdown fences, no explanation.\n\n"
            f"Schema:\n{schema_str}"
        )
        raw, _ = self.generate_with_tools(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    def get_embeddings(self, text: Union[str, List[str]]) -> List[List[float]]:
        raise NotImplementedError(
            "AnthropicAdapter does not support embeddings. "
            "Inject GeminiAdapter into PineconeExecutor for embedding generation."
        )

    def stream_response(self, prompt: str):
        raise NotImplementedError(
            "Streaming not yet implemented in AnthropicAdapter."
        )

    def classify_intent(self, user_query: str) -> str:
        response = self.generate_response(
            prompt=(
                "Classify this query as exactly one of: quant, qual, synthesis.\n\n"
                f"Query: {user_query}\n\nRespond with one word only."
            ),
            system_message="You are a financial query classifier.",
        )
        return response.strip().lower()
