"""
ingest_fragments.py -- chunk + tag + embed the earnings corpus into
tagged, embedded fragments for two-stage retrieval.

Reads:
  backend/data/earnings_releases/ticker={TICKER}.parquet

Writes:
  backend/data/earnings_fragments/ticker={TICKER}.parquet

Idempotent: skips (source_id, tagger_version, embedding_version) triples
already present. Re-runnable safely.

Usage:
    python backend/scripts/ingest_fragments.py                # whole universe
    python backend/scripts/ingest_fragments.py --tickers NVDA
    python backend/scripts/ingest_fragments.py --tickers MU QCOM --limit 3
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("ingest_fragments")

_REPO_ROOT        = Path(__file__).resolve().parents[2]
_RELEASES_DIR     = _REPO_ROOT / "backend" / "data" / "earnings_releases"


def _discover_universe() -> list[str]:
    if not _RELEASES_DIR.exists():
        return []
    return sorted(p.stem.replace("ticker=", "") for p in _RELEASES_DIR.glob("ticker=*.parquet"))


def _normalize_exhibit(exhibit: str) -> str:
    import re
    m = re.match(r"EX-99\.+0*(\d+)", exhibit or "")
    return f"EX-99.{m.group(1)}" if m else (exhibit or "")


def _source_type_from_exhibit(exhibit: str) -> str:
    norm = _normalize_exhibit(exhibit)
    if norm == "EX-99.1":
        return "press_release"
    if norm == "EX-99.2":
        return "cfo_commentary"
    return "press_release"


def ingest_ticker(
    ticker: str,
    *,
    tagger,
    embedder,
    fragment_store,
    fiscal_map_fn,
    limit: int | None = None,
) -> dict:
    """
    Chunk + tag + embed all earnings releases for one ticker.
    Skips source documents already tagged at the current versions.

    Performance: collects all chunks across all releases first, then tags
    them in bulk (parallel batches inside FragmentTagger.tag_batch) and
    embeds them in bulk. This amortizes LLM per-request overhead across
    the whole ticker at once.
    """
    from backend.app.services.research.chunker import chunk_document
    from backend.app.services.research.fragment_store import EMBEDDING_VERSION
    from backend.app.services.research.taxonomy import TAXONOMY_VERSION

    path = _RELEASES_DIR / f"ticker={ticker}.parquet"
    if not path.exists():
        return {"ticker": ticker, "error": "no releases parquet"}
    df = pd.read_parquet(path)
    df = df.sort_values("filing_date", ascending=False)
    if limit:
        df = df.head(limit)

    existing = fragment_store.existing_source_ids(ticker, TAXONOMY_VERSION, EMBEDDING_VERSION)
    fmap = fiscal_map_fn(ticker)

    # Phase 1: chunk every release; build a flat list of chunk records with
    # enough metadata that we can regroup them into rows after tag+embed.
    all_tag_inputs: list[dict] = []
    chunk_meta: list[dict] = []      # parallel to all_tag_inputs
    processed_sources = 0

    for _, r in df.iterrows():
        exhibit    = str(r["exhibit"])
        accession  = str(r["accession_no"])
        source_id  = f"{ticker}:{accession}:{exhibit}"
        if source_id in existing:
            continue

        text_raw = str(r.get("text_raw", ""))
        if not text_raw:
            continue

        chunks = chunk_document(text_raw)
        if not chunks:
            continue

        filing_date = pd.Timestamp(r["filing_date"])
        src_type    = _source_type_from_exhibit(exhibit)
        fp_label    = _fiscal_period_for(filing_date, fmap)

        for c in chunks:
            cid = str(uuid.uuid4())
            all_tag_inputs.append({"chunk_id": cid, "text": c.text})
            chunk_meta.append({
                "chunk_id":      cid,
                "source_id":     source_id,
                "source_type":   src_type,
                "filing_date":   filing_date,
                "fiscal_period": fp_label,
                "char_start":    int(c.char_start),
                "char_end":      int(c.char_end),
                "token_count":   int(c.token_count),
                "kind":          c.kind,
                "text":          c.text,
            })
        processed_sources += 1

    total_chunks = len(chunk_meta)
    log.info("  %s: %d sources → %d chunks to process", ticker, processed_sources, total_chunks)

    if total_chunks == 0:
        return {
            "ticker":            ticker,
            "processed_sources": 0,
            "total_chunks":      0,
            "new_rows":          0,
            "skipped_cached":    len(existing),
        }

    # Phase 2: tag all chunks in bulk (parallel batches)
    log.info("  %s: tagging...", ticker)
    tagged = tagger.tag_batch(all_tag_inputs)
    tag_by_id = {t["chunk_id"]: t["tags"] for t in tagged}

    # Phase 3: embed all chunks in bulk
    log.info("  %s: embedding...", ticker)
    vectors = embedder.embed_texts([m["text"] for m in chunk_meta])

    # Phase 4: assemble and persist rows
    now = pd.Timestamp(datetime.now(timezone.utc))
    new_rows: list[dict] = []
    for meta, vec in zip(chunk_meta, vectors):
        new_rows.append({
            "fragment_id":       meta["chunk_id"],
            "ticker":            ticker,
            "source_id":         meta["source_id"],
            "source_type":       meta["source_type"],
            "filing_date":       meta["filing_date"],
            "fiscal_period":     meta["fiscal_period"],
            "char_start":        meta["char_start"],
            "char_end":          meta["char_end"],
            "token_count":       meta["token_count"],
            "kind":              meta["kind"],
            "text":              meta["text"],
            "tags":              list(tag_by_id.get(meta["chunk_id"], ["other"])),
            "tagger_version":    TAXONOMY_VERSION,
            "embedding":         list(vec),
            "embedding_model":   embedder.model_name,
            "embedding_version": EMBEDDING_VERSION,
            "created_at":        now,
        })

    fragment_store.append_rows(ticker, new_rows)

    return {
        "ticker":            ticker,
        "processed_sources": processed_sources,
        "total_chunks":      total_chunks,
        "new_rows":          len(new_rows),
        "skipped_cached":    len(existing),
    }


def _fiscal_period_for(filing_date: pd.Timestamp, fmap: list[tuple[pd.Timestamp, str]]) -> str | None:
    candidate: str | None = None
    for end_date, label in fmap:
        if end_date <= filing_date:
            candidate = label
        else:
            break
    return candidate


def _build_fiscal_map(ticker: str) -> list[tuple[pd.Timestamp, str]]:
    calc_path = _REPO_ROOT / "backend" / "data" / "filing_data" / "calculated" / f"ticker={ticker}.parquet"
    if not calc_path.exists():
        return []
    try:
        df = pd.read_parquet(calc_path, columns=["end_date", "fiscal_year", "fiscal_quarter", "is_ytd"])
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="Tickers to ingest (default: whole universe)")
    ap.add_argument("--limit", type=int, default=None, help="Limit releases per ticker (for testing)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    # Bootstrap LLM and adapters
    sys.path.insert(0, str(_REPO_ROOT))
    from backend.app.api.dependencies import get_engine_llm
    from backend.app.services.research.embedder import Embedder
    from backend.app.services.research.fragment_store import FragmentStore
    from backend.app.services.research.tagger import FragmentTagger

    llm = get_engine_llm()
    tagger = FragmentTagger(llm=llm)
    embedder = Embedder(llm=llm)
    store = FragmentStore()

    tickers = [t.upper() for t in (args.tickers or _discover_universe())]
    if not tickers:
        log.error("No tickers provided and universe is empty.")
        return 1

    log.info("Ingesting fragments for %d tickers", len(tickers))
    log.info("Embedding model: %s", embedder.model_name)
    log.info("")

    for t in tickers:
        try:
            r = ingest_ticker(
                t,
                tagger=tagger,
                embedder=embedder,
                fragment_store=store,
                fiscal_map_fn=_build_fiscal_map,
                limit=args.limit,
            )
            log.info(
                "%s: +%d rows (%d sources processed, %d cached, %d total chunks)",
                r["ticker"], r.get("new_rows", 0), r.get("processed_sources", 0),
                r.get("skipped_cached", 0), r.get("total_chunks", 0),
            )
        except Exception as e:
            log.error("%s FAILED: %s", t, e, exc_info=args.verbose)

    return 0


if __name__ == "__main__":
    sys.exit(main())
