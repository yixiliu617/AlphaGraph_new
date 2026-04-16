from backend.app.models.domain.data_fragment import DataFragment
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.graph_repository import VectorRepository


def store_fragment(
    fragment: DataFragment,
    db: DBRepository,
    llm: LLMProvider,
    vector_db: VectorRepository,
) -> None:
    """
    Step 4: Persist the validated DataFragment to both the relational store
    (state & lineage) and the vector store (semantic search).

    Swapping Pinecone for another vector DB only touches VectorRepository's
    adapter — this step's interface stays unchanged.
    """
    db.save_fragment(fragment)

    embedding = llm.get_embeddings(fragment.content["raw_text"])
    vector_db.upsert_vectors(
        vectors=embedding,
        metadata=[_slim_metadata(fragment)],
    )


def _slim_metadata(fragment: DataFragment) -> dict:
    """
    Build a compact metadata payload for Pinecone.

    Pinecone enforces a 40KB per-vector metadata limit, so we cannot dump the
    entire fragment (which may include long raw_text and nested extracted_metrics).
    We keep only the fields needed for filtering and result rendering in the
    Engine, plus a short raw_text preview for snippet display.
    """
    content = fragment.content if isinstance(fragment.content, dict) else {}
    metrics = content.get("extracted_metrics", {}) if isinstance(content, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}

    raw_text = content.get("raw_text", "") or ""

    return {
        "fragment_id":        str(fragment.fragment_id),
        "tenant_id":          fragment.tenant_id,
        "source_type":        fragment.source_type,
        "source":             fragment.source,
        "exact_location":     fragment.exact_location or "",
        "source_document_id": str(metrics.get("source_document_id", "")),
        "document_title":     str(metrics.get("source_article_title", ""))[:200],
        "document_author":    str(metrics.get("source_article_author", "")),
        "document_date":      str(metrics.get("source_article_date", "")),
        "raw_text_preview":   raw_text[:500],
    }
