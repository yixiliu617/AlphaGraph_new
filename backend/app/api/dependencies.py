from fastapi import Depends
from sqlalchemy.orm import Session
from backend.app.db.session import get_db_session
from backend.app.core.config import settings


# ---------------------------------------------------------------------------
# Adapter factories — one function per adapter.
# Swapping an implementation means editing exactly one factory, nothing else.
# ---------------------------------------------------------------------------

def get_db_repo(db: Session = Depends(get_db_session)):
    from backend.app.adapters.db.postgres_adapter import PostgresAdapter
    return PostgresAdapter(db)


def get_quant_repo():
    from backend.app.adapters.db.duckdb_adapter import DuckDBAdapter
    return DuckDBAdapter(data_path=settings.DUCKDB_PATH)


def get_llm_provider():
    if settings.ACTIVE_LLM == "gemini":
        from backend.app.adapters.llm.gemini_adapter import GeminiAdapter
        return GeminiAdapter(api_key=settings.GEMINI_API_KEY)
    raise ValueError(f"LLM provider '{settings.ACTIVE_LLM}' is not configured.")


def get_vector_repo():
    from backend.app.adapters.vector.pinecone_adapter import PineconeAdapter
    return PineconeAdapter(
        api_key=settings.PINECONE_API_KEY,
        index_name=settings.PINECONE_INDEX_NAME,
    )


def get_graph_repo():
    from backend.app.adapters.graph.neo4j_adapter import Neo4jAdapter
    return Neo4jAdapter(
        uri=settings.NEO4J_URI,
        user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD,
    )


# ---------------------------------------------------------------------------
# Service factories — compose adapters into services/agents.
# ---------------------------------------------------------------------------

def get_extraction_runner(
    db=Depends(get_db_repo),
    llm=Depends(get_llm_provider),
    vector=Depends(get_vector_repo),
    graph=Depends(get_graph_repo),
):
    from backend.app.services.extraction_engine.runner import ExtractionRunner
    return ExtractionRunner(db=db, llm=llm, vector_db=vector, graph_db=graph)


def get_engine_llm():
    """
    Returns the LLM adapter for the Engine agent based on ENGINE_LLM setting.

    Switching providers: set ENGINE_LLM in .env --
      ENGINE_LLM=anthropic   (default) requires ANTHROPIC_API_KEY
      ENGINE_LLM=gemini                requires GEMINI_API_KEY
      ENGINE_LLM=openai                requires OPENAI_API_KEY

    ENGINE_MODEL overrides the default model for the chosen provider.
    """
    provider = (settings.ENGINE_LLM or "anthropic").lower()
    model_override = settings.ENGINE_MODEL  # None = use adapter default

    if provider == "anthropic":
        from backend.app.adapters.llm.anthropic_adapter import AnthropicAdapter
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ENGINE_LLM=anthropic but ANTHROPIC_API_KEY is not set.")
        kwargs = {"api_key": settings.ANTHROPIC_API_KEY}
        if model_override:
            kwargs["model"] = model_override
        return AnthropicAdapter(**kwargs)

    if provider == "gemini":
        from backend.app.adapters.llm.gemini_adapter import GeminiAdapter
        if not settings.GEMINI_API_KEY:
            raise ValueError("ENGINE_LLM=gemini but GEMINI_API_KEY is not set.")
        kwargs = {"api_key": settings.GEMINI_API_KEY}
        if model_override:
            kwargs["model_name"] = model_override
        return GeminiAdapter(**kwargs)

    if provider == "openai":
        from backend.app.adapters.llm.openai_adapter import OpenAIAdapter
        if not settings.OPENAI_API_KEY:
            raise ValueError("ENGINE_LLM=openai but OPENAI_API_KEY is not set.")
        kwargs = {"api_key": settings.OPENAI_API_KEY}
        if model_override:
            kwargs["model"] = model_override
        return OpenAIAdapter(**kwargs)

    raise ValueError(
        f"Unknown ENGINE_LLM value: '{settings.ENGINE_LLM}'. "
        "Valid options: anthropic, gemini, openai"
    )


def get_engine_agent(
    vector=Depends(get_vector_repo),
):
    """
    Factory for the EngineAgent (Unified Data Engine).

    Engine LLM is selected via ENGINE_LLM in .env (default: anthropic).
    Embeddings for PineconeExecutor always use GeminiAdapter (the only adapter
    that supports embeddings). If GEMINI_API_KEY is absent, Pinecone search
    is skipped gracefully.

    To add a new data source executor: instantiate it and append to the list.
    """
    from backend.app.agents.router_agent import EngineAgent
    from backend.app.agents.executors.data_agent_executor import DataAgentExecutor
    from backend.app.agents.executors.pinecone_executor import PineconeExecutor
    from backend.app.agents.executors.executor_registry import ExecutorRegistry

    engine_llm = get_engine_llm()
    executors  = [DataAgentExecutor()]

    # PineconeExecutor always uses GeminiAdapter for embeddings regardless of
    # which Engine LLM is active -- AnthropicAdapter and OpenAIAdapter use
    # different embedding models and Pinecone index was populated with Gemini vectors.
    if settings.GEMINI_API_KEY and settings.PINECONE_API_KEY:
        from backend.app.adapters.llm.gemini_adapter import GeminiAdapter
        embedding_llm = GeminiAdapter(api_key=settings.GEMINI_API_KEY)
        executors.append(PineconeExecutor(llm=embedding_llm, vector=vector))

    registry = ExecutorRegistry(executors=executors)
    return EngineAgent(llm=engine_llm, registry=registry)


def get_alert_service(
    db=Depends(get_db_repo),
    llm=Depends(get_llm_provider),
):
    from backend.app.services.alert_service import AlertService
    return AlertService(db=db, llm=llm)


# ---------------------------------------------------------------------------
# Insights module — isolated block.
# To remove the insights feature: delete this block + insights.py + the
# insights import/include_router lines in main.py + the import in session.py.
# Nothing else in this file is touched.
# ---------------------------------------------------------------------------

def get_insight_repo(db: Session = Depends(get_db_session)):
    from backend.app.adapters.db.insight_postgres_adapter import InsightPostgresAdapter
    return InsightPostgresAdapter(db)


def get_insight_runner(
    insight_repo=Depends(get_insight_repo),
    db=Depends(get_db_repo),
    llm=Depends(get_llm_provider),
):
    from backend.app.services.insights.runner import InsightRunner
    return InsightRunner(insight_repo=insight_repo, db_repo=db, llm=llm)


def get_margin_insights_service():
    """
    Factory for MarginInsightsService (Data Explorer Phase B).
    Uses the Engine LLM (ENGINE_LLM in .env) since this is a narrative
    synthesis task, not a classification task. DataAgent and
    MarginInsightsCache are constructed with defaults.
    """
    from backend.app.services.insights.margin_insights_service import MarginInsightsService
    return MarginInsightsService(llm=get_engine_llm())
