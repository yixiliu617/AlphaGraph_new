"""
gemini_adapter.py -- Google Gemini LLM adapter.

Used for:
  - Extraction pipeline (generate_response, generate_structured_output)
  - Embeddings for Pinecone (get_embeddings) -- only adapter that supports this
  - Engine agent tool-use when ENGINE_LLM=gemini (generate_with_tools)

Accepts neutral TOOL_SPECS from tools.py and converts to Gemini format.

To activate for the Engine:
    Set ENGINE_LLM=gemini in .env (GEMINI_API_KEY already required for extraction)
"""
import json
from typing import List, Any, Dict, Union

import google.generativeai as genai

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.agents.tools import to_gemini_tools


class GeminiAdapter(LLMProvider):
    """
    ADAPTER: Google Gemini. Implements the full LLMProvider port.

    Supports:
      - Text generation, structured output, embeddings (all existing uses)
      - Tool-use via generate_with_tools() (Engine agent)
    """

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        genai.configure(api_key=api_key)
        self._model_name  = model_name
        self._embed_model = "models/gemini-embedding-001"
        # Base model (no system instruction) -- used for embeddings + classify_intent
        self.model = genai.GenerativeModel(model_name)
        print(f"Gemini Adapter initialized with model: {model_name}")

    # ------------------------------------------------------------------
    # Tool-use (Engine agent)
    # ------------------------------------------------------------------

    def generate_with_tools(
        self,
        system: str,
        messages: List[Dict],
        tools: List[Dict],
    ) -> tuple:
        """
        One Gemini turn with optional function calling.
        tools should be TOOL_SPECS (neutral format) -- converted here.
        Returns (text: str, tool_calls: list[{name, input}]).

        Notes:
          - System instruction is set via system_instruction= on the model object.
          - Message format converted: {role, content} -> {role, parts: [{text}]}
          - "assistant" role mapped to "model" (Gemini convention).
        """
        # Create a model instance with the system instruction for this call
        model = (
            genai.GenerativeModel(self._model_name, system_instruction=system)
            if system else self.model
        )

        gemini_messages = _to_gemini_messages(messages)
        kwargs: dict = {"contents": gemini_messages}

        if tools:
            kwargs["tools"] = to_gemini_tools(tools)
            kwargs["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}

        response = model.generate_content(**kwargs)

        text: str = ""
        tool_calls: list[dict] = []

        # Gemini may return multiple candidates; use the first
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    tool_calls.append({
                        "name":  part.function_call.name,
                        "input": dict(part.function_call.args),
                    })
                elif hasattr(part, "text") and part.text:
                    text += part.text

        return text, tool_calls

    # ------------------------------------------------------------------
    # Standard LLMProvider methods (extraction pipeline, embeddings)
    # ------------------------------------------------------------------

    def generate_response(
        self,
        prompt: str,
        system_message: str = "You are a senior financial analyst.",
    ) -> str:
        full_prompt = f"{system_message}\n\nUser Question: {prompt}"
        response = self.model.generate_content(full_prompt)
        return response.text

    def generate_structured_output(
        self,
        prompt: str,
        output_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        schema_text = json.dumps(output_schema, indent=2)
        full_prompt = (
            f"You are a financial data extractor. Output MUST strictly follow this JSON Schema:\n"
            f"{schema_text}\n\nInput Text: {prompt}\n\nJSON Output:"
        )
        response = self.model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json"
            ),
        )
        return json.loads(response.text)

    def get_embeddings(self, text: Union[str, List[str]]) -> List[List[float]]:
        if isinstance(text, str):
            text = [text]
        result = genai.embed_content(
            model=self._embed_model,
            content=text,
            task_type="retrieval_document",
        )
        return result["embedding"]

    def stream_response(self, prompt: str):
        response = self.model.generate_content(prompt, stream=True)
        for chunk in response:
            yield chunk.text

    def classify_intent(self, user_query: str) -> str:
        prompt = (
            f"Categorize this financial query as 'quant' (numbers/math), "
            f"'qual' (text/sentiment), or 'synthesis' (portfolio logic).\n"
            f"Query: {user_query}\n\nCategory:"
        )
        response = self.model.generate_content(prompt)
        category = response.text.strip().lower()
        if "quant" in category:
            return "quant"
        if "qual" in category:
            return "qual"
        return "synthesis"


# ---------------------------------------------------------------------------
# Message format converter
# ---------------------------------------------------------------------------

def _to_gemini_messages(messages: List[Dict]) -> List[Dict]:
    """
    Convert standard {role, content} messages to Gemini {role, parts} format.
    Maps "assistant" -> "model" (Gemini's convention).
    Skips system messages (handled via system_instruction= on the model).
    """
    role_map = {"user": "user", "assistant": "model", "model": "model"}
    result = []
    for msg in messages:
        role    = role_map.get(msg.get("role", "user"), "user")
        content = msg.get("content", "")
        if not content:
            continue
        result.append({"role": role, "parts": [{"text": content}]})
    return result
