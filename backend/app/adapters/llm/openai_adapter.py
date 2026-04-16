"""
openai_adapter.py -- OpenAI LLM adapter (GPT-4o, o1, etc.).

Implements the full LLMProvider port including tool-use for the Engine agent.
Accepts neutral TOOL_SPECS from tools.py and converts to OpenAI format.

Embeddings: supported via text-embedding-3-small (can replace Gemini embeddings
if preferred, though Gemini embeddings are already configured for Pinecone).

To activate for the Engine:
    Set ENGINE_LLM=openai in .env
    Set OPENAI_API_KEY=sk-... in .env
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Union

from openai import OpenAI

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.agents.tools import to_openai_tools

_DEFAULT_MODEL           = "gpt-4o"
_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class OpenAIAdapter(LLMProvider):
    """
    ADAPTER: OpenAI GPT. Implements the full LLMProvider port.

    Supports:
      - Text generation, structured output, embeddings
      - Tool-use via generate_with_tools() (Engine agent)
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
    ):
        self._client          = OpenAI(api_key=api_key)
        self._model           = model
        self._embedding_model = embedding_model
        print(f"OpenAI Adapter initialized with model: {model}")

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
        One OpenAI chat turn with optional function calling.
        tools should be TOOL_SPECS (neutral format) -- converted here.
        Returns (text: str, tool_calls: list[{name, input}]).

        System message is prepended as {"role": "system", "content": system}.
        """
        openai_messages: list[dict] = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(messages)   # already in {role, content} format

        kwargs: dict = {
            "model":    self._model,
            "messages": openai_messages,
        }
        if tools:
            kwargs["tools"]       = to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        choice   = response.choices[0]
        msg      = choice.message

        text: str = msg.content or ""
        tool_calls: list[dict] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "name":  tc.function.name,
                    "input": json.loads(tc.function.arguments),
                })

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
        if isinstance(text, str):
            text = [text]
        response = self._client.embeddings.create(
            model=self._embedding_model,
            input=text,
        )
        return [item.embedding for item in response.data]

    def stream_response(self, prompt: str):
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def classify_intent(self, user_query: str) -> str:
        response = self.generate_response(
            prompt=(
                "Classify this query as exactly one of: quant, qual, synthesis.\n\n"
                f"Query: {user_query}\n\nRespond with one word only."
            ),
            system_message="You are a financial query classifier.",
        )
        return response.strip().lower()
