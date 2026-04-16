"""
Embedding helper around any LLM adapter that exposes `get_embeddings`.

Two embedding tasks matter for retrieval:
  - document embeddings (stored at ingest, task_type="retrieval_document")
  - query embeddings    (computed per-request, task_type="retrieval_query")

Gemini uses task_type hints natively. OpenAI uses a single model for both.
The adapter interface abstracts that — we just call get_embeddings(texts).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_BATCH_SIZE = 50  # embeddings per API call


class Embedder:
    def __init__(self, llm: Any):
        self.llm = llm
        self.model_name = self._resolve_model_name(llm)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts. Batches to avoid oversized API calls.
        Returns embeddings in the same order as input.
        """
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            vecs = self.llm.get_embeddings(batch)
            if isinstance(vecs, list) and vecs and isinstance(vecs[0], (int, float)):
                # Single-vector result — adapter returned a flat list (shouldn't
                # happen with a list input, but be defensive).
                out.append(list(vecs))
            else:
                out.extend([list(v) for v in vecs])
        return out

    def embed_one(self, text: str) -> list[float]:
        vecs = self.embed_texts([text])
        return vecs[0] if vecs else []

    @staticmethod
    def _resolve_model_name(llm: Any) -> str:
        for attr in ("_embed_model", "_embedding_model", "embedding_model_name", "model_name"):
            v = getattr(llm, attr, None)
            if isinstance(v, str) and v:
                return v
        return type(llm).__name__
