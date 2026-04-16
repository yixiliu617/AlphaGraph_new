"""
Persistence + retrieval layer for tagged, embedded document fragments.

Storage: backend/data/earnings_fragments/ticker={TICKER}.parquet
  One row per chunk. zstd-compressed. Columns:

    fragment_id:        str  (uuid — composite key within ticker)
    ticker:             str
    source_id:          str  (FK to earnings_releases: TICKER:accession:exhibit)
    source_type:        str  (press_release | cfo_commentary | mdna | transcript_*)
    filing_date:        date
    fiscal_period:      str | null
    char_start:         int  (char offset in the source text_raw)
    char_end:           int
    token_count:        int
    kind:               str  (paragraph | list | table | header)
    text:               str
    tags:               list[str]   (multi-tag, 1-3 items from the taxonomy)
    tagger_version:     str
    embedding:          list[float]
    embedding_model:    str
    embedding_version:  str
    created_at:         ts

Retrieval: two-stage
  1. Deterministic filter by ticker / date / source_type / tags
  2. Cosine similarity against the filtered subset, return top-K

Brute-force cosine is fine at this scale (~17K vectors universe-wide).
No vector-store dependency.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_REPO_ROOT     = Path(__file__).resolve().parents[4]
_FRAGMENTS_DIR = _REPO_ROOT / "backend" / "data" / "earnings_fragments"

EMBEDDING_VERSION = "v1"


class FragmentStore:
    def __init__(self):
        _FRAGMENTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def path(self, ticker: str) -> Path:
        return _FRAGMENTS_DIR / f"ticker={ticker.upper()}.parquet"

    def load_ticker(self, ticker: str) -> pd.DataFrame:
        p = self.path(ticker)
        if not p.exists():
            return pd.DataFrame()
        try:
            return pd.read_parquet(p)
        except Exception as e:
            log.warning("Could not read fragments for %s: %s", ticker, e)
            return pd.DataFrame()

    def existing_source_ids(self, ticker: str, tagger_version: str, embedding_version: str) -> set[str]:
        df = self.load_ticker(ticker)
        if df.empty:
            return set()
        mask = (
            (df["tagger_version"] == tagger_version)
            & (df["embedding_version"] == embedding_version)
        )
        return set(df.loc[mask, "source_id"].astype(str).tolist())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append_rows(self, ticker: str, rows: list[dict]) -> None:
        if not rows:
            return
        new_df = pd.DataFrame(rows)
        existing = self.load_ticker(ticker)
        if existing.empty:
            combined = new_df
        else:
            combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["fragment_id"], keep="last"
        )
        combined = combined.sort_values(["filing_date", "source_id", "char_start"])
        combined = combined.reset_index(drop=True)
        combined.to_parquet(
            self.path(ticker),
            compression="zstd",
            compression_level=9,
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search(
        self,
        ticker: str,
        query_embedding: list[float],
        *,
        lookback_years: float | None = None,
        source_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        top_k: int = 20,
        min_cosine: float = 0.0,
    ) -> pd.DataFrame:
        """
        Two-stage retrieval.

        1. Load ticker's fragments, filter by date / source_type / tags.
        2. Rank filtered rows by cosine similarity vs. query_embedding.

        Returns a DataFrame of the top-K matching fragments, sorted by
        descending cosine, with a 'score' column added.

        tags_any: optional list of tag slugs. If provided, a fragment is kept
        only if at least one of its tags appears in this list. Pass None to
        disable tag filtering (pure semantic).
        """
        df = self.load_ticker(ticker)
        if df.empty:
            return df

        # Date filter
        if lookback_years is not None:
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=int(lookback_years))
            df = df[df["filing_date"] >= cutoff]

        # Source-type filter
        if source_types:
            df = df[df["source_type"].isin(source_types)]

        # Tag filter: keep rows whose tags list intersects tags_any.
        # "other"-only chunks are always excluded from retrieval.
        if tags_any:
            def _match(tags_list) -> bool:
                try:
                    return any(t in tags_any for t in tags_list)
                except Exception:
                    return False
            df = df[df["tags"].apply(_match)]
        else:
            def _not_only_other(tags_list) -> bool:
                try:
                    return not (len(tags_list) == 1 and tags_list[0] == "other")
                except Exception:
                    return True
            df = df[df["tags"].apply(_not_only_other)]

        if df.empty:
            return df

        # Cosine similarity
        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return df.head(0)
        q = q / q_norm

        vecs = np.asarray(df["embedding"].tolist(), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1)
        norms = np.where(norms == 0, 1.0, norms)
        vecs_n = vecs / norms[:, None]
        scores = vecs_n @ q

        df = df.copy()
        df["score"] = scores
        df = df[df["score"] >= min_cosine]
        df = df.sort_values("score", ascending=False).head(top_k).reset_index(drop=True)
        return df
