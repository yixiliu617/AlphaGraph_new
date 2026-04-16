"""
LLM-based multi-tag classifier for document fragments.

Given a list of chunks and the canonical taxonomy, the tagger assigns 1-3
tags from TAGS.keys() to each chunk in a single batched LLM call.

Usage:
    tagger = FragmentTagger(llm=get_engine_llm())
    tagged = tagger.tag_batch(chunks)  # returns list[dict] with tags added

Batching: up to BATCH_SIZE chunks per LLM call. Reduces overhead from one
call per chunk (17K calls) to ~1.7K calls for the whole universe.

The prompt pins the allowed tag slugs, shows the descriptions, and gives
six few-shot examples illustrating multi-tagging.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
from typing import Any

from backend.app.services.research.chunker import Chunk
from backend.app.services.research.taxonomy import (
    TAG_SLUGS,
    TAGS,
    TAXONOMY_VERSION,
    build_few_shot_block,
    build_prompt_taxonomy_block,
)

log = logging.getLogger(__name__)

# 25 chunks per batch balances per-call latency against per-call overhead.
# MAX_WORKERS=8 runs eight tagger calls concurrently via a thread pool;
# Gemini SDK is blocking per-call but releases the GIL during the HTTP wait,
# so threading delivers real parallelism for the network-bound path.
BATCH_SIZE = 25
MAX_WORKERS = 8


_TAG_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Echo the chunk_id field from the input",
                    },
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": TAG_SLUGS,
                        },
                        "description": "1 to 3 tags from the allowed list, in descending order of centrality to the chunk",
                    },
                },
                "required": ["chunk_id", "tags"],
            },
        }
    },
    "required": ["results"],
}


class FragmentTagger:
    def __init__(self, llm: Any):
        self.llm = llm
        self.taxonomy_version = TAXONOMY_VERSION

    def tag_batch(self, chunks: list[dict]) -> list[dict]:
        """
        chunks: list of dicts with at least 'chunk_id' and 'text' keys.
        Returns: the same list, with a 'tags' list[str] added to each.
                 Chunks the tagger fails to classify get tags=["other"].

        Dispatches batches in parallel via a thread pool to reduce total
        wall time. Each LLM call is still a blocking request; concurrency
        hides the per-request latency.
        """
        if not chunks:
            return chunks

        batches = [
            chunks[i : i + BATCH_SIZE]
            for i in range(0, len(chunks), BATCH_SIZE)
        ]

        # Each batch maps to a list[dict] result, or an exception-handled
        # fallback where every chunk gets tags=["other"].
        def process(batch):
            try:
                return self._tag_single_batch(batch)
            except Exception as e:
                log.warning("Tagger batch failed (%d chunks): %s", len(batch), e)
                fallback = []
                for c in batch:
                    c = dict(c)
                    c["tags"] = ["other"]
                    fallback.append(c)
                return fallback

        out: list[dict] = []
        if len(batches) == 1:
            out.extend(process(batches[0]))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                for result in ex.map(process, batches):
                    out.extend(result)
        return out

    def _tag_single_batch(self, batch: list[dict]) -> list[dict]:
        # Build the input block
        input_lines = []
        for c in batch:
            input_lines.append(
                f'chunk_id: {c["chunk_id"]}\n'
                f'text: """{c["text"]}"""\n'
            )
        input_block = "\n---\n".join(input_lines)

        prompt = (
            "You are a tagging classifier for financial earnings-release text. "
            "Assign 1 to 3 tags from the allowed taxonomy below to each chunk.\n\n"
            "Rules:\n"
            "  - Multi-tagging is REQUIRED when a chunk covers multiple topics. "
            "A chunk about 'AI-driven hyperscaler demand with pricing tailwinds' "
            "must get demand_commentary, ai_commentary, and pricing_dynamics — not "
            "just one of them.\n"
            "  - Order tags by centrality: most central tag first.\n"
            "  - Use the 'other' tag ONLY for boilerplate, safe harbor, footnotes, "
            "cover sheets, signatures, and contact info. Never use 'other' alongside "
            "another tag.\n"
            "  - Return ONLY tags from the allowed list — do not invent new ones.\n\n"
            "Allowed tags (slug: description):\n"
            f"{build_prompt_taxonomy_block()}\n\n"
            "Few-shot examples:\n"
            f"{build_few_shot_block()}\n"
            "Now tag these chunks. Return JSON matching the schema with one "
            "result per chunk, using the chunk_id field to identify which chunk "
            "each result refers to.\n\n"
            f"Chunks:\n{input_block}"
        )

        raw = self.llm.generate_structured_output(prompt, _TAG_SCHEMA)
        results = raw.get("results", []) if isinstance(raw, dict) else []

        # Index results by chunk_id for robust joining (LLM can return in any order)
        results_by_id = {}
        for r in results:
            cid = str(r.get("chunk_id", ""))
            tags = r.get("tags", []) or []
            # Validate: keep only tags that exist in the taxonomy
            clean = [t for t in tags if t in TAGS][:3]
            if not clean:
                clean = ["other"]
            results_by_id[cid] = clean

        tagged: list[dict] = []
        for c in batch:
            c = dict(c)
            c["tags"] = results_by_id.get(c["chunk_id"], ["other"])
            tagged.append(c)
        return tagged
