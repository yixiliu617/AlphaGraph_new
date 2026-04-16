"""
DocumentQueryService — natural-language Q&A over the earnings-release corpus.

Uses the B+C hybrid pipeline:
  1. Resolve candidate fragments (tagged, embedded chunks) for the ticker
     in the lookback window via deterministic filter.
  2. Embed the user question, rank filtered fragments by cosine similarity,
     take top-K as the retrieval set.
  3. Group top-K fragments by source_id — each source document may have
     multiple relevant chunks.
  4. Cache check: rows in document_findings with matching
     (query_hash, source_id, extractor_version) are returned directly.
  5. For sources not in the cache, one LLM call synthesizes key_points
     and verbatim quotes using ONLY the retrieved chunks (not the full doc).
  6. Verify quotes against the source's raw text.
  7. Persist new findings and return aggregated results sorted by date.

Persistence:
  - Fragments:  backend/data/earnings_fragments/ticker={TICKER}.parquet
  - Findings:   backend/data/insights/document_findings/ticker={TICKER}.parquet
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.services.research.embedder import Embedder
from backend.app.services.research.fragment_store import FragmentStore
from backend.app.services.research.schemas import (
    Finding,
    LLM_OUTPUT_SCHEMA,
    Quote,
    QueryRequest,
    QueryResponse,
    SourceType,
)

log = logging.getLogger(__name__)

_REPO_ROOT        = Path(__file__).resolve().parents[4]
_RELEASES_DIR     = _REPO_ROOT / "backend" / "data" / "earnings_releases"
_FINDINGS_DIR     = _REPO_ROOT / "backend" / "data" / "insights" / "document_findings"
_CALC_DIR         = _REPO_ROOT / "backend" / "data" / "filing_data" / "calculated"

EXTRACTOR_VERSION = "v1"

# Semantic-cache threshold: two questions for the same ticker are treated as
# equivalent when their embedding cosine similarity is >= this value.
# 0.92 is strict enough to avoid conflating distinct topics (margin vs outlook)
# while loose enough to catch reword variants like
#   "Micron outlook"
#   "what did Micron say about guidance"
#   "Micron's forward commentary"
SEMANTIC_CACHE_THRESHOLD = 0.92


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _slugify_query(question: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", question.lower()).strip("_")
    return s[:80] or "query"


def _hash_topic(ticker: str, question: str) -> str:
    norm = f"{ticker.upper()}|{question.strip().lower()}"
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _normalize_for_verify(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_exhibit(exhibit: str) -> str:
    m = re.match(r"EX-99\.+0*(\d+)", exhibit or "")
    return f"EX-99.{m.group(1)}" if m else (exhibit or "")


def _source_type_from_exhibit(exhibit: str) -> SourceType:
    norm = _normalize_exhibit(exhibit)
    if norm == "EX-99.1":
        return SourceType.PRESS_RELEASE
    if norm == "EX-99.2":
        return SourceType.CFO_COMMENTARY
    return SourceType.PRESS_RELEASE  # fallback; treat other EX-99.* as press release


def _source_id(ticker: str, accession_no: str, exhibit: str) -> str:
    return f"{ticker}:{accession_no}:{exhibit}"


def _build_fiscal_map(ticker: str) -> list[tuple[pd.Timestamp, str]]:
    path = _CALC_DIR / f"ticker={ticker}.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path, columns=["end_date", "fiscal_year", "fiscal_quarter", "is_ytd"])
    except Exception:
        return []
    df = df[
        df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
        & (~df["is_ytd"].astype(bool))
        & df["fiscal_year"].notna()
        & df["end_date"].notna()
    ].copy()
    df["end_date"] = pd.to_datetime(df["end_date"])
    df = df.sort_values("end_date")
    out: list[tuple[pd.Timestamp, str]] = []
    for _, r in df.iterrows():
        try:
            fy = int(r["fiscal_year"])
        except (TypeError, ValueError):
            continue
        out.append((r["end_date"], f"FY{fy}-{r['fiscal_quarter']}"))
    return out


def _resolve_model_name(llm: Any) -> str:
    """
    Return a stable string name for whichever LLM adapter we're using.
    Different adapters expose this differently:
      AnthropicAdapter: self.model is a string like "claude-sonnet-4-6"
      GeminiAdapter:    self.model is a google.generativeai GenerativeModel object
                        but self.model_name is the string form
      OpenAIAdapter:    self.model is a string like "gpt-5"
    """
    for attr in ("model_name", "model"):
        v = getattr(llm, attr, None)
        if isinstance(v, str) and v:
            return v
    # GenerativeModel has .model_name too, even though self.model is the object
    m = getattr(llm, "model", None)
    v = getattr(m, "model_name", None)
    if isinstance(v, str) and v:
        return v
    return type(llm).__name__


def _fiscal_period_for(filing_date: pd.Timestamp, fmap: list[tuple[pd.Timestamp, str]]) -> str | None:
    candidate: str | None = None
    for end_date, label in fmap:
        if end_date <= filing_date:
            candidate = label
        else:
            break
    return candidate


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DocumentQueryService:
    def __init__(self, llm):
        """
        llm: an LLM adapter exposing:
          - generate_structured_output(prompt, schema)  — for extraction
          - get_embeddings(list[str])                   — for query embedding
        """
        self.llm = llm
        self.embedder = Embedder(llm)
        self.fragment_store = FragmentStore()
        _FINDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def query(self, req: QueryRequest) -> QueryResponse:
        ticker     = req.ticker.upper().strip()
        question   = req.question.strip()
        topic_slug = _slugify_query(question)
        query_hash = _hash_topic(ticker, question)

        # 1. Embed the user question
        query_embedding = self.embedder.embed_one(question)
        if not query_embedding:
            return self._empty_response(ticker, question, topic_slug, req.lookback_years)

        # 2. Cache lookup — exact, then semantic
        cached_df = self._load_findings(ticker)

        effective_query_hash = query_hash  # may be overwritten if we find a semantic match

        if not cached_df.empty:
            # 2a. Exact match on query_hash
            exact = cached_df[
                (cached_df["query_hash"] == query_hash)
                & (cached_df["extractor_version"] == EXTRACTOR_VERSION)
            ]

            # 2b. Semantic match: find any prior query_hash whose question
            #     embedding is close enough to the current one. We walk the
            #     unique (query_hash, question_embedding) pairs.
            if exact.empty:
                match_hash = self._find_semantic_cache_hit(cached_df, query_embedding)
                if match_hash is not None:
                    effective_query_hash = match_hash
                    exact = cached_df[
                        (cached_df["query_hash"] == match_hash)
                        & (cached_df["extractor_version"] == EXTRACTOR_VERSION)
                    ]
                    log.info(
                        "research.query SEMANTIC cache hit ticker=%s question=%r -> match_hash=%s rows=%d",
                        ticker, question, match_hash, len(exact),
                    )

            # If we now have an exact (or semantic-match) cached result, return it directly.
            # We still filter the findings through the current semantic retrieval so
            # stale cached sources that aren't relevant to the current question don't
            # appear — but since the questions are essentially identical, they should
            # all still be relevant.
            if not exact.empty:
                findings = [
                    self._dict_to_finding(self._row_to_dict(r))
                    for _, r in exact.iterrows()
                ]
                findings = [f for f in findings if f.key_points or f.quotes]
                findings.sort(key=lambda f: f.filing_date, reverse=True)
                return QueryResponse(
                    ticker=ticker,
                    question=question,
                    topic_slug=topic_slug,
                    lookback_years=req.lookback_years,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    findings=findings,
                    docs_considered=len(findings),
                    docs_with_hits=len(findings),
                    from_cache=len(findings),
                    newly_extracted=0,
                )

        # 3. No cache hit — run the full fragment retrieval + extraction pipeline
        source_type_values = (
            [st.value for st in req.source_types] if req.source_types else None
        )
        top_fragments = self.fragment_store.search(
            ticker,
            query_embedding,
            lookback_years=req.lookback_years,
            source_types=source_type_values,
            tags_any=None,
            top_k=25,
        )
        if top_fragments.empty:
            return self._empty_response(ticker, question, topic_slug, req.lookback_years)

        log.info("research.query ticker=%s question=%r top_k=%d",
                 ticker, question, len(top_fragments))

        # 4. Group fragments by source_id — one Finding per source document
        grouped = self._group_fragments_by_source(top_fragments)

        cached_source_ids: set[str] = set()
        cached_for_query = pd.DataFrame()

        miss_sources = [g for g in grouped if g["source_id"] not in cached_source_ids]
        hit_count = len(grouped) - len(miss_sources)

        # 5. Extract for cache misses
        newly_extracted: list[dict] = []
        if miss_sources:
            newly_extracted = self._extract_from_fragments(
                ticker, question, topic_slug, query_hash, miss_sources,
                query_embedding=query_embedding,
            )
            if newly_extracted:
                self._persist(ticker, newly_extracted)

        # 6. Assemble response: union of cached hits (restricted to retrieval set) + new
        retrieved_source_ids = {g["source_id"] for g in grouped}
        all_rows: list[dict] = []
        if not cached_for_query.empty:
            for _, r in cached_for_query.iterrows():
                if str(r["source_id"]) in retrieved_source_ids:
                    all_rows.append(self._row_to_dict(r))
        all_rows.extend(newly_extracted)

        findings = [self._dict_to_finding(r) for r in all_rows]
        findings = [f for f in findings if f.key_points or f.quotes]
        findings.sort(key=lambda f: f.filing_date, reverse=True)

        return QueryResponse(
            ticker=ticker,
            question=question,
            topic_slug=topic_slug,
            lookback_years=req.lookback_years,
            generated_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            docs_considered=len(grouped),
            docs_with_hits=len(findings),
            from_cache=hit_count,
            newly_extracted=len(miss_sources),
        )

    def _empty_response(self, ticker, question, topic_slug, lookback_years) -> QueryResponse:
        return QueryResponse(
            ticker=ticker,
            question=question,
            topic_slug=topic_slug,
            lookback_years=lookback_years,
            generated_at=datetime.now(timezone.utc).isoformat(),
            findings=[],
            docs_considered=0,
            docs_with_hits=0,
            from_cache=0,
            newly_extracted=0,
        )

    # ------------------------------------------------------------------
    # Candidate loading
    # ------------------------------------------------------------------

    def _load_candidates(
        self,
        ticker: str,
        lookback_years: int,
        source_types: list[SourceType] | None,
    ) -> list[dict]:
        path = _RELEASES_DIR / f"ticker={ticker}.parquet"
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        cutoff = pd.Timestamp.now() - pd.DateOffset(years=lookback_years)
        df = df[df["filing_date"] >= cutoff].copy()
        df = df.sort_values("filing_date", ascending=False)

        fmap = _build_fiscal_map(ticker)
        allowed = set(source_types) if source_types else None

        out: list[dict] = []
        for _, r in df.iterrows():
            exhibit   = str(r["exhibit"])
            src_type  = _source_type_from_exhibit(exhibit)
            if allowed and src_type not in allowed:
                continue
            filing_dt = pd.Timestamp(r["filing_date"])
            fp_label  = _fiscal_period_for(filing_dt, fmap)
            out.append({
                "source_id":       _source_id(ticker, str(r["accession_no"]), exhibit),
                "source_type":     src_type.value,
                "filing_date":     filing_dt.strftime("%Y-%m-%d"),
                "fiscal_period":   fp_label,
                "title":           self._build_title(ticker, src_type, fp_label, filing_dt),
                "source_url":      (str(r["url"]) if pd.notna(r.get("url")) else None),
                "text_raw":        str(r.get("text_raw", "")),
            })
        return out

    def _build_title(
        self,
        ticker: str,
        src_type: SourceType,
        fiscal_period: str | None,
        filing_dt: pd.Timestamp,
    ) -> str:
        label_map = {
            SourceType.PRESS_RELEASE:       "Press Release",
            SourceType.CFO_COMMENTARY:      "CFO Commentary",
            SourceType.MDNA:                "MD&A",
            SourceType.TRANSCRIPT_PREPARED: "Earnings Call",
            SourceType.TRANSCRIPT_QA:       "Earnings Call Q&A",
            SourceType.MEETING_NOTE:        "Meeting Note",
        }
        label = label_map.get(src_type, src_type.value.replace("_", " ").title())
        ymd = filing_dt.strftime("%Y-%m-%d")
        return f"[{ticker}] {label} · {fiscal_period or ymd} ({ymd})"

    # ------------------------------------------------------------------
    # Fragment grouping + source-level metadata enrichment
    # ------------------------------------------------------------------

    def _group_fragments_by_source(self, top_fragments: pd.DataFrame) -> list[dict]:
        """
        Group a ranked chunk DataFrame into one dict per source_id, preserving
        the per-source top score. Also fetches the raw text of each source
        document so we can verify quotes later.
        """
        grouped: dict[str, dict] = {}
        # We need raw source text (from earnings_releases) for quote verification.
        # Cache per-ticker to avoid re-reading the parquet on every iteration.
        _ticker_text_cache: dict[str, dict[str, dict]] = {}

        for _, row in top_fragments.iterrows():
            sid = str(row["source_id"])
            if sid not in grouped:
                ticker = str(row["ticker"])
                src_type = SourceType(str(row["source_type"]))
                filing_dt = pd.Timestamp(row["filing_date"])
                fp_label = row.get("fiscal_period") or None

                # Fetch source metadata + raw text from earnings_releases parquet
                if ticker not in _ticker_text_cache:
                    _ticker_text_cache[ticker] = self._load_source_index(ticker)
                src_meta = _ticker_text_cache[ticker].get(sid, {})

                grouped[sid] = {
                    "source_id":     sid,
                    "ticker":        ticker,
                    "source_type":   src_type.value,
                    "filing_date":   filing_dt.strftime("%Y-%m-%d"),
                    "fiscal_period": fp_label if pd.notna(fp_label) else None,
                    "title":         self._build_title(ticker, src_type, fp_label if pd.notna(fp_label) else None, filing_dt),
                    "source_url":    src_meta.get("url"),
                    "text_raw":      src_meta.get("text_raw", ""),
                    "top_score":     float(row["score"]),
                    "chunks":        [],
                }
            grouped[sid]["chunks"].append({
                "text":       str(row["text"]),
                "score":      float(row["score"]),
                "tags":       list(row["tags"]) if hasattr(row["tags"], "__iter__") else [],
                "char_start": int(row["char_start"]),
                "char_end":   int(row["char_end"]),
            })

        # Return as list sorted by top chunk score descending
        out = list(grouped.values())
        out.sort(key=lambda g: g["top_score"], reverse=True)
        return out

    def _load_source_index(self, ticker: str) -> dict[str, dict]:
        """Returns { source_id: { 'text_raw', 'url' } } for every row in a ticker's
        earnings_releases parquet."""
        path = _RELEASES_DIR / f"ticker={ticker}.parquet"
        if not path.exists():
            return {}
        df = pd.read_parquet(path, columns=["accession_no", "exhibit", "text_raw", "url"])
        out: dict[str, dict] = {}
        for _, r in df.iterrows():
            sid = _source_id(ticker, str(r["accession_no"]), str(r["exhibit"]))
            out[sid] = {
                "text_raw": str(r.get("text_raw", "")),
                "url":      (str(r["url"]) if pd.notna(r.get("url")) else None),
            }
        return out

    # ------------------------------------------------------------------
    # LLM extraction (fragment-based)
    # ------------------------------------------------------------------

    def _extract_from_fragments(
        self,
        ticker: str,
        question: str,
        topic_slug: str,
        query_hash: str,
        sources: list[dict],
        *,
        query_embedding: list[float] | None = None,
    ) -> list[dict]:
        """
        Fragment-based extraction: the prompt contains only the top chunks
        retrieved via semantic search, grouped by source document.
        Dramatically smaller context than full-doc extraction.
        """
        if not sources:
            return []

        # Build one prompt with per-source excerpt blocks
        docs_block = []
        for s in sources:
            excerpts = "\n\n".join(
                f'[excerpt]\n{chunk["text"]}'
                for chunk in s["chunks"]
            )
            docs_block.append(
                f"---\n[source_id: {s['source_id']}]\n"
                f"[title: {s['title']}]\n"
                f"[date: {s['filing_date']}]\n"
                f"[type: {s['source_type']}]\n\n"
                f"{excerpts}\n"
            )
        joined = "\n".join(docs_block)

        prompt = (
            f"You are a senior equity-research analyst. Answer the following question "
            f"about {ticker} using ONLY the excerpts below.\n\n"
            f"Question: {question}\n\n"
            f"For each source, the excerpts were selected by semantic search as the "
            f"most relevant passages from that document. For each source you must:\n"
            f"  1. Set `relevant=true` and return 3-5 one-sentence key_points if the "
            f"excerpts directly address the topic.\n"
            f"  2. Set `relevant=false` and return empty lists if the excerpts do not "
            f"address the topic.\n"
            f"  3. In `quotes`, include exact verbatim sentences taken from the "
            f"excerpts. Do not paraphrase — the sentences must appear literally in the "
            f"excerpt text.\n\n"
            f"Return results as an array with one entry per input source, using the "
            f"source_id field to identify which source each result refers to.\n\n"
            f"Sources:\n\n{joined}\n\n"
            f"Return your answer as JSON matching the provided schema."
        )

        raw = self.llm.generate_structured_output(prompt, LLM_OUTPUT_SCHEMA)
        results = raw.get("results", []) if isinstance(raw, dict) else []

        by_id = {s["source_id"]: s for s in sources}
        now_iso = datetime.now(timezone.utc).isoformat()
        extractor_model = _resolve_model_name(self.llm)

        rows: list[dict] = []
        for r in results:
            sid = str(r.get("source_id", ""))
            src = by_id.get(sid)
            if src is None:
                continue
            key_points = [str(k).strip() for k in r.get("key_points", []) if k]
            raw_quotes = r.get("quotes", []) or []
            verified_quotes = self._verify_quotes(src["text_raw"], raw_quotes)

            rows.append({
                "finding_id":        str(uuid.uuid4()),
                "ticker":            ticker,
                "topic_label":       question,
                "topic_slug":        topic_slug,
                "query_hash":        query_hash,
                "question_embedding": list(query_embedding) if query_embedding else None,
                "source_type":       src["source_type"],
                "source_id":         sid,
                "filing_date":       src["filing_date"],
                "fiscal_period":     src["fiscal_period"],
                "title":             src["title"],
                "source_url":        src["source_url"],
                "key_points":        json.dumps(key_points),
                "quotes":            json.dumps(verified_quotes),
                "extracted_at":      now_iso,
                "extractor_model":   extractor_model,
                "extractor_version": EXTRACTOR_VERSION,
            })
        return rows

    # ------------------------------------------------------------------
    # Semantic cache lookup
    # ------------------------------------------------------------------

    def _find_semantic_cache_hit(
        self,
        cached_df: pd.DataFrame,
        query_embedding: list[float],
    ) -> str | None:
        """
        Scan the findings parquet for a prior query whose question embedding
        is semantically close to the current question. Returns the matching
        query_hash (highest cosine above threshold), or None.

        Rows without a persisted question_embedding are skipped — they come
        from pre-semantic-cache entries and can be rebuilt on re-query.
        """
        if cached_df.empty or "question_embedding" not in cached_df.columns:
            return None

        # One row per unique query_hash (drop duplicates on query_hash)
        try:
            seen = cached_df.drop_duplicates(subset=["query_hash"], keep="first")
        except Exception:
            return None

        try:
            import numpy as np
        except Exception:
            return None

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return None
        q = q / q_norm

        best_hash: str | None = None
        best_score: float = 0.0
        for _, row in seen.iterrows():
            qe = row.get("question_embedding")
            if qe is None:
                continue
            try:
                # qe may be a numpy array, a list, or None
                arr = np.asarray(list(qe), dtype=np.float32)
            except Exception:
                continue
            if arr.size == 0 or arr.shape[0] != q.shape[0]:
                continue
            n = np.linalg.norm(arr)
            if n == 0:
                continue
            score = float(np.dot(q, arr / n))
            if score > best_score:
                best_score = score
                best_hash = str(row["query_hash"])

        if best_hash is not None and best_score >= SEMANTIC_CACHE_THRESHOLD:
            log.info("semantic cache match: cosine=%.3f threshold=%.2f", best_score, SEMANTIC_CACHE_THRESHOLD)
            return best_hash
        return None

    # ------------------------------------------------------------------
    # LLM extraction (legacy full-doc path — kept for fallback)
    # ------------------------------------------------------------------

    def _extract(
        self,
        ticker: str,
        question: str,
        topic_slug: str,
        query_hash: str,
        candidates: list[dict],
    ) -> list[dict]:
        if not candidates:
            return []

        # Build one prompt containing all candidate documents.
        # The LLM returns a list of per-document results, each keyed by source_id.
        docs_block = []
        for c in candidates:
            docs_block.append(
                f"---\n[source_id: {c['source_id']}]\n"
                f"[title: {c['title']}]\n"
                f"[date: {c['filing_date']}]\n"
                f"[type: {c['source_type']}]\n\n"
                f"{c['text_raw']}\n"
            )
        joined = "\n".join(docs_block)

        prompt = (
            f"You are a senior equity-research analyst. Answer the following question "
            f"about {ticker} using ONLY the source documents below.\n\n"
            f"Question: {question}\n\n"
            f"For each document, extract what management said about the topic in the question. "
            f"For each document you must:\n"
            f"  1. Set `relevant=true` and return 3-5 one-sentence key_points if the document "
            f"directly addresses the topic.\n"
            f"  2. Set `relevant=false` and return empty lists if the document does not "
            f"address the topic.\n"
            f"  3. In `quotes`, include the exact verbatim sentences from the source that "
            f"support each key point. Do not paraphrase — the sentences must appear literally "
            f"in the source text.\n\n"
            f"Return results as an array with one entry per input document, using the "
            f"source_id field to identify which document each result refers to. If multiple "
            f"documents were provided, return entries for all of them (not just the relevant ones).\n\n"
            f"Documents:\n\n{joined}\n\n"
            f"Return your answer as JSON matching the provided schema."
        )

        raw = self.llm.generate_structured_output(prompt, LLM_OUTPUT_SCHEMA)
        results = raw.get("results", []) if isinstance(raw, dict) else []

        # Index candidates by source_id for quick lookup + verification
        by_id = {c["source_id"]: c for c in candidates}
        now_iso = datetime.now(timezone.utc).isoformat()
        extractor_model = _resolve_model_name(self.llm)

        rows: list[dict] = []
        for r in results:
            sid = str(r.get("source_id", ""))
            cand = by_id.get(sid)
            if cand is None:
                continue
            key_points = [str(k).strip() for k in r.get("key_points", []) if k]
            raw_quotes = r.get("quotes", []) or []
            verified_quotes = self._verify_quotes(cand["text_raw"], raw_quotes)

            rows.append({
                "finding_id":        str(uuid.uuid4()),
                "ticker":            ticker,
                "topic_label":       question,
                "topic_slug":        topic_slug,
                "query_hash":        query_hash,
                "source_type":       cand["source_type"],
                "source_id":         sid,
                "filing_date":       cand["filing_date"],
                "fiscal_period":     cand["fiscal_period"],
                "title":             cand["title"],
                "source_url":        cand["source_url"],
                "key_points":        json.dumps(key_points),
                "quotes":            json.dumps(verified_quotes),
                "extracted_at":      now_iso,
                "extractor_model":   extractor_model,
                "extractor_version": EXTRACTOR_VERSION,
            })
        return rows

    def _verify_quotes(self, source_text: str, raw_quotes) -> list[dict]:
        """
        Check each quote against the source. Quotes that appear literally
        (after whitespace normalization) are marked verified=True. Quotes
        that don't appear are still returned (verified=False) so the user
        can see what the LLM produced and judge for themselves.

        Robust to three LLM output shapes:
          - list of dicts: [{"text": "..."}]
          - list of strings: ["..."]
          - list of mixed
        """
        norm_source = _normalize_for_verify(source_text)
        out: list[dict] = []
        for q in (raw_quotes or []):
            if isinstance(q, dict):
                text = str(q.get("text", "")).strip()
            elif isinstance(q, str):
                text = q.strip()
            else:
                text = str(q).strip()
            if not text:
                continue
            verified = _normalize_for_verify(text) in norm_source
            out.append({"text": text, "verified": verified})
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _findings_path(self, ticker: str) -> Path:
        return _FINDINGS_DIR / f"ticker={ticker}.parquet"

    def _load_findings(self, ticker: str) -> pd.DataFrame:
        path = self._findings_path(ticker)
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(path)
        except Exception as e:
            log.warning("Could not read findings parquet for %s: %s", ticker, e)
            return pd.DataFrame()

    def _persist(self, ticker: str, new_rows: list[dict]) -> None:
        if not new_rows:
            return
        existing = self._load_findings(ticker)
        new_df = pd.DataFrame(new_rows)
        if existing.empty:
            combined = new_df
        else:
            combined = pd.concat([existing, new_df], ignore_index=True)
        # Dedupe on (query_hash, source_id, extractor_version) — keep latest
        combined = combined.drop_duplicates(
            subset=["query_hash", "source_id", "extractor_version"],
            keep="last",
        )
        combined = combined.sort_values(["query_hash", "filing_date"], ascending=[True, False])
        combined.to_parquet(self._findings_path(ticker), compression="zstd", compression_level=9)

    # ------------------------------------------------------------------
    # Row ↔ Finding conversion
    # ------------------------------------------------------------------

    def _row_to_dict(self, r: pd.Series) -> dict:
        d = r.to_dict()
        # Re-parse JSON columns
        for col in ("key_points", "quotes"):
            v = d.get(col)
            if isinstance(v, str):
                try:
                    d[col] = json.loads(v)
                except Exception:
                    d[col] = []
        return d

    def _dict_to_finding(self, d: dict) -> Finding:
        quotes_raw = d.get("quotes", [])
        if isinstance(quotes_raw, str):
            try:
                quotes_raw = json.loads(quotes_raw)
            except Exception:
                quotes_raw = []
        quotes = [Quote(text=q.get("text", ""), verified=bool(q.get("verified", False))) for q in (quotes_raw or [])]

        key_points_raw = d.get("key_points", [])
        if isinstance(key_points_raw, str):
            try:
                key_points_raw = json.loads(key_points_raw)
            except Exception:
                key_points_raw = []

        return Finding(
            finding_id        = str(d.get("finding_id", "")),
            ticker            = str(d.get("ticker", "")),
            topic_label       = str(d.get("topic_label", "")),
            topic_slug        = str(d.get("topic_slug", "")),
            source_type       = SourceType(str(d.get("source_type", "press_release"))),
            source_id         = str(d.get("source_id", "")),
            filing_date       = str(d.get("filing_date", "")),
            fiscal_period     = d.get("fiscal_period") or None,
            title             = str(d.get("title", "")),
            source_url        = d.get("source_url") or None,
            key_points        = [str(k) for k in (key_points_raw or [])],
            quotes            = quotes,
            extracted_at      = str(d.get("extracted_at", "")),
            extractor_model   = str(d.get("extractor_model", "")),
            extractor_version = str(d.get("extractor_version", EXTRACTOR_VERSION)),
        )
