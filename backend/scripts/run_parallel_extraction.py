"""
Parallel Extraction Runner
===========================

Runs all four extraction modules simultaneously on a broker report PDF
using a ThreadPoolExecutor.

Modules:
  1. Causal Relationship   -- cause-effect chains -> CAUSES graph edges
  2. Chart Extraction      -- chart images + vision LLM -> PNG files + fragments
  3. Company Intel         -- primary + peer business segments/metrics -> HAS_SEGMENT / HAS_PRODUCT / COMPARED_TO edges
  4. Business Relationship -- inter-company links -> SUPPLIES_TO / CUSTOMER_OF / COMPETES_WITH / PARTNERS_WITH / MENTIONED_WITH edges

Usage:
  # Process first PDF found in Broker_report/
  python -m backend.scripts.run_parallel_extraction

  # Process a specific file
  python -m backend.scripts.run_parallel_extraction path/to/report.pdf

  # Process ALL PDFs in Broker_report/
  python -m backend.scripts.run_parallel_extraction --all

  # Skip services not yet configured
  python -m backend.scripts.run_parallel_extraction --skip-pinecone --skip-neo4j

  # Custom tenant
  python -m backend.scripts.run_parallel_extraction --tenant my-fund

Services used:
  - SQLite/Postgres (always on)  -- stores fragments, recipes
  - Pinecone (if PINECONE_API_KEY set)  -- vector embeddings for Engine search
  - Neo4j   (if reachable)       -- graph edges for Topology tab
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import settings
from backend.app.adapters.llm.gemini_adapter import GeminiAdapter
from backend.app.adapters.db.postgres_adapter import PostgresAdapter
from backend.app.db.session import SessionLocal, init_db

from backend.scripts.extractors.doc_metadata import extract_document_metadata
from backend.scripts.extractors.causal_extractor        import make_causal_recipe,        run_causal_extraction
from backend.scripts.extractors.chart_extractor         import make_chart_recipe,         run_chart_extraction
from backend.scripts.extractors.company_intel_extractor import make_company_intel_recipe,  run_company_intel_extraction
from backend.scripts.extractors.relationship_extractor  import make_relationship_recipe,   run_relationship_extraction

BROKER_REPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "Broker_report"
CHART_OUTPUT_DIR  = Path(__file__).resolve().parents[1] / "data" / "extracted_charts"
TENANT_ID         = "alphagraph-system"


# ---------------------------------------------------------------------------
# Service detection
# ---------------------------------------------------------------------------

def _try_pinecone():
    """Returns a real PineconeAdapter if PINECONE_API_KEY is set, else None."""
    if not settings.PINECONE_API_KEY:
        return None, "PINECONE_API_KEY not set"
    try:
        from backend.app.adapters.vector.pinecone_adapter import PineconeAdapter
        adapter = PineconeAdapter(
            api_key=settings.PINECONE_API_KEY,
            index_name=settings.PINECONE_INDEX_NAME,
        )
        return adapter, "ok"
    except Exception as e:
        return None, str(e)


def _try_neo4j():
    """Returns a real Neo4jAdapter if Neo4j is reachable, else None."""
    try:
        from backend.app.adapters.graph.neo4j_adapter import Neo4jAdapter
        adapter = Neo4jAdapter(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
        )
        # Quick connectivity check
        with adapter.driver.session() as s:
            s.run("RETURN 1")
        return adapter, "ok"
    except Exception as e:
        return None, str(e)


def _mock_vector():
    m = MagicMock()
    m.upsert_vectors.return_value = True
    return m


def _mock_graph():
    m = MagicMock()
    m.add_relationship.return_value = True
    return m


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------

def _make_adapters(pinecone_adapter, neo4j_adapter):
    """
    Fresh, independent adapters for one thread.
    SQLAlchemy session is not thread-safe -- each thread gets its own.
    Pinecone and Neo4j adapters are shared (they are thread-safe).
    """
    session  = SessionLocal()
    db       = PostgresAdapter(session)
    llm      = GeminiAdapter(api_key=settings.GEMINI_API_KEY)
    vector_db = pinecone_adapter if pinecone_adapter is not None else _mock_vector()
    graph_db  = neo4j_adapter    if neo4j_adapter    is not None else _mock_graph()
    return db, llm, vector_db, graph_db, session


def _close_session(session) -> None:
    try:
        session.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Module wrappers (one per module -- each runs in its own thread)
# ---------------------------------------------------------------------------

def _run_causal(pdf_path: Path, doc_meta: dict, pinecone_adapter, neo4j_adapter) -> List:
    db, llm, vector_db, graph_db, session = _make_adapters(pinecone_adapter, neo4j_adapter)
    try:
        recipe = make_causal_recipe(TENANT_ID)
        db.save_recipe(recipe)
        return run_causal_extraction(
            pdf_path=pdf_path, recipe=recipe, llm=llm,
            db=db, vector_db=vector_db, graph_db=graph_db, doc_meta=doc_meta,
        )
    finally:
        _close_session(session)


def _run_chart(pdf_path: Path, doc_meta: dict, pinecone_adapter, neo4j_adapter) -> List:
    db, llm, vector_db, graph_db, session = _make_adapters(pinecone_adapter, neo4j_adapter)
    try:
        recipe = make_chart_recipe(TENANT_ID)
        db.save_recipe(recipe)
        return run_chart_extraction(
            pdf_path=pdf_path, recipe=recipe,
            gemini_api_key=settings.GEMINI_API_KEY,
            llm=llm, db=db, vector_db=vector_db,
            output_dir=CHART_OUTPUT_DIR, doc_meta=doc_meta,
        )
    finally:
        _close_session(session)


def _run_company_intel(pdf_path: Path, doc_meta: dict, pinecone_adapter, neo4j_adapter) -> List:
    db, llm, vector_db, graph_db, session = _make_adapters(pinecone_adapter, neo4j_adapter)
    try:
        recipe = make_company_intel_recipe(TENANT_ID)
        db.save_recipe(recipe)
        return run_company_intel_extraction(
            pdf_path=pdf_path, recipe=recipe, llm=llm,
            db=db, vector_db=vector_db, graph_db=graph_db, doc_meta=doc_meta,
        )
    finally:
        _close_session(session)


def _run_relationship(pdf_path: Path, doc_meta: dict, pinecone_adapter, neo4j_adapter) -> List:
    db, llm, vector_db, graph_db, session = _make_adapters(pinecone_adapter, neo4j_adapter)
    try:
        recipe = make_relationship_recipe(TENANT_ID)
        db.save_recipe(recipe)
        return run_relationship_extraction(
            pdf_path=pdf_path, recipe=recipe, llm=llm,
            db=db, vector_db=vector_db, graph_db=graph_db, doc_meta=doc_meta,
        )
    finally:
        _close_session(session)


# ---------------------------------------------------------------------------
# Single PDF extraction
# ---------------------------------------------------------------------------

def extract_one(
    pdf_path: Path,
    tenant_id: str,
    pinecone_adapter,
    neo4j_adapter,
) -> dict:
    """
    Runs all four modules in parallel on one PDF.
    Returns a summary dict: {fragments_by_module, errors, elapsed, doc_meta}.
    """
    t0 = time.perf_counter()

    print(f"\n[Meta] Extracting document metadata...")
    meta_llm = GeminiAdapter(api_key=settings.GEMINI_API_KEY)
    doc_meta = extract_document_metadata(pdf_path, meta_llm)
    print(f"  Title  : {doc_meta['document_title']}")
    print(f"  Author : {doc_meta['document_author']}")
    print(f"  Date   : {doc_meta['document_date']}")
    print(f"  Point  : {doc_meta['document_main_point'][:100]}...")
    print(f"  Doc ID : {doc_meta['source_document_id']}")

    MODULE_RUNNERS = {
        "causal":        (_run_causal,        pdf_path, doc_meta, pinecone_adapter, neo4j_adapter),
        "chart":         (_run_chart,         pdf_path, doc_meta, pinecone_adapter, neo4j_adapter),
        "company_intel": (_run_company_intel, pdf_path, doc_meta, pinecone_adapter, neo4j_adapter),
        "relationship":  (_run_relationship,  pdf_path, doc_meta, pinecone_adapter, neo4j_adapter),
    }

    results: dict[str, List] = {}
    errors:  dict[str, str]  = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures: dict[Future, str] = {
            pool.submit(fn, *fn_args): module_name
            for module_name, (fn, *fn_args) in MODULE_RUNNERS.items()
        }
        for future in as_completed(futures):
            module = futures[future]
            try:
                results[module] = future.result()
            except Exception as e:
                errors[module]  = str(e)
                results[module] = []
                print(f"\n[{module.upper()}] FAILED: {e}")

    return {
        "doc_meta":    doc_meta,
        "results":     results,
        "errors":      errors,
        "elapsed":     time.perf_counter() - t0,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaGraph Parallel Extractor")
    parser.add_argument(
        "pdf_path", nargs="?",
        help="Path to a single broker report PDF. Omit to use first PDF in Broker_report/.",
    )
    parser.add_argument("--all",          action="store_true", help="Process all PDFs in Broker_report/.")
    parser.add_argument("--tenant",       default=TENANT_ID,   help="Tenant ID (default: alphagraph-system).")
    parser.add_argument("--skip-pinecone",action="store_true", help="Skip Pinecone upsert even if key is set.")
    parser.add_argument("--skip-neo4j",  action="store_true", help="Skip Neo4j even if reachable.")
    args = parser.parse_args()

    # --- Validate inputs ------------------------------------------------------
    if not settings.GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    if args.pdf_path:
        pdfs = [Path(args.pdf_path)]
        if not pdfs[0].exists():
            print(f"ERROR: File not found: {pdfs[0]}")
            sys.exit(1)
    elif args.all:
        pdfs = sorted(BROKER_REPORT_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {BROKER_REPORT_DIR}")
            sys.exit(1)
    else:
        pdfs = sorted(BROKER_REPORT_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No PDFs found in {BROKER_REPORT_DIR}")
            sys.exit(1)
        pdfs = [pdfs[0]]

    # --- Initialise database --------------------------------------------------
    print("Initialising database tables...")
    init_db()
    print("  Database ready.")

    # --- Service detection ----------------------------------------------------
    print("\nService status:")

    pinecone_adapter = None
    if args.skip_pinecone:
        print("  Pinecone : SKIPPED (--skip-pinecone)")
    else:
        pinecone_adapter, msg = _try_pinecone()
        if pinecone_adapter:
            print(f"  Pinecone : CONNECTED (index={settings.PINECONE_INDEX_NAME})")
        else:
            print(f"  Pinecone : MOCK -- {msg}")
            print("             Fragments stored in DB only. Set PINECONE_API_KEY to enable vector search.")

    neo4j_adapter = None
    if args.skip_neo4j:
        print("  Neo4j    : SKIPPED (--skip-neo4j)")
    else:
        neo4j_adapter, msg = _try_neo4j()
        if neo4j_adapter:
            print(f"  Neo4j    : CONNECTED ({settings.NEO4J_URI})")
        else:
            print(f"  Neo4j    : MOCK -- {msg}")
            print("             Graph edges not written. Start Neo4j to enable Topology tab.")

    # --- Create output dir for charts -----------------------------------------
    CHART_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Process PDFs ---------------------------------------------------------
    print()
    grand_total = 0

    for idx, pdf_path in enumerate(pdfs):
        print("=" * 60)
        print(f"PDF {idx + 1}/{len(pdfs)}: {pdf_path.name}")
        print("=" * 60)

        summary = extract_one(pdf_path, args.tenant, pinecone_adapter, neo4j_adapter)

        elapsed = summary["elapsed"]
        results = summary["results"]
        errors  = summary["errors"]

        print(f"\nResults for {pdf_path.name} ({elapsed:.1f}s):")
        module_labels = {
            "causal":        "Module 1 (Causal)",
            "chart":         "Module 2 (Charts)",
            "company_intel": "Module 3 (Company Intel)",
            "relationship":  "Module 4 (Relationship)",
        }
        pdf_total = 0
        for key, label in module_labels.items():
            ids = results.get(key, [])
            pdf_total += len(ids)
            status = errors.get(key, "")
            suffix = f"  ERROR: {status}" if status else ""
            print(f"  {label:<30} {len(ids)} fragment(s){suffix}")

        saved_charts = sorted(CHART_OUTPUT_DIR.glob("*.png"))
        if saved_charts:
            print(f"  Chart PNGs saved: {len(saved_charts)}")

        if errors:
            print("\n  Errors:")
            for module, msg in errors.items():
                print(f"    [{module}] {msg}")

        print(f"  Total this file: {pdf_total} fragment(s)")
        grand_total += pdf_total

    # --- Final summary --------------------------------------------------------
    print()
    print("=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"Files processed : {len(pdfs)}")
    print(f"Total fragments : {grand_total}")
    print(f"Pinecone        : {'real' if pinecone_adapter else 'mock (not indexed)'}")
    print(f"Neo4j           : {'real' if neo4j_adapter else 'mock (not written)'}")
    print()

    if not pinecone_adapter:
        print("-> To enable Engine document search:")
        print("   1. Create a Pinecone index named '{}' (768 dims, cosine metric)".format(
            settings.PINECONE_INDEX_NAME))
        print("   2. Set PINECONE_API_KEY=... in your .env")
        print("   3. Re-run this script to index the fragments")
    if not neo4j_adapter:
        print("-> To enable Topology Graph:")
        print("   1. Start Neo4j (default bolt://localhost:7687)")
        print("   2. Set NEO4J_PASSWORD=... in your .env if non-default")
        print("   3. Re-run this script to write graph edges")


if __name__ == "__main__":
    main()
