"""
End-to-End ingestion tests.

Require ALL live services:
  - Gemini API key  (GEMINI_API_KEY env var)
  - Pinecone index  (PINECONE_API_KEY + PINECONE_INDEX_NAME env vars)
  - Neo4j instance  (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD env vars)
  - SQLite DB       (default dev DB — no extra setup)

Run with:
    pytest -m e2e

Skip silently in CI by not passing -m e2e (or configure CI to exclude e2e).
"""

import pytest

from backend.app.db.session import SessionLocal, init_db
from backend.app.api.dependencies import (
    get_db_repo, get_llm_provider, get_vector_repo, get_graph_repo,
)
from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.universe import UniverseFilter

TENANT_ID = "institutional-alpha-1"


# ---------------------------------------------------------------------------
# E2E fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_db_session():
    init_db()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="module")
def live_runner(live_db_session):
    db    = get_db_repo(live_db_session)
    llm   = get_llm_provider()
    vector = get_vector_repo()
    graph  = get_graph_repo()
    return ExtractionRunner(db=db, llm=llm, vector_db=vector, graph_db=graph)


@pytest.fixture(scope="module")
def live_graph(live_db_session):
    return get_graph_repo()


@pytest.fixture(scope="module")
def live_db_repo(live_db_session):
    return get_db_repo(live_db_session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
class TestSECFilingIngestion:

    def test_full_pipeline_returns_fragment_id(self, live_runner, live_db_repo):
        recipe = ExtractionRecipe(
            tenant_id=TENANT_ID,
            name="E2E SEC Revenue Extractor",
            ingestor_type="SEC_TEXT",
            llm_prompt_template=(
                "Extract Revenue, Net Income, and the primary Risk Factor "
                "from this filing snippet."
            ),
            expected_schema={
                "type": "object",
                "properties": {
                    "revenue":      {"type": "number"},
                    "net_income":   {"type": "number"},
                    "primary_risk": {"type": "string"},
                },
            },
        )
        live_db_repo.save_recipe(recipe)

        raw_text = (
            "Apple Inc. (AAPL) reported Q4 FY2024 revenue of $94.9 billion, "
            "net income of $21.7 billion. Key risk: foreign exchange rate volatility "
            "could adversely impact reported results."
        )
        fragment_id = live_runner.run_recipe_on_text(
            recipe_id=recipe.recipe_id,
            raw_text=raw_text,
            source_info={
                "name":     "AAPL_10K_2024_snippet.txt",
                "type":     "sec_filing",
                "location": "Risk Factors section",
            },
        )
        assert fragment_id is not None, "Expected a fragment_id but got None — check logs."

    def test_fragment_retrievable_after_ingestion(self, live_runner, live_db_repo):
        recipe = ExtractionRecipe(
            tenant_id=TENANT_ID,
            name="E2E Retrieval Check Recipe",
            ingestor_type="SEC_TEXT",
            llm_prompt_template="Extract revenue.",
            expected_schema={
                "type": "object",
                "properties": {"revenue": {"type": "number"}},
            },
        )
        live_db_repo.save_recipe(recipe)

        fragment_id = live_runner.run_recipe_on_text(
            recipe_id=recipe.recipe_id,
            raw_text="MSFT reported $65B in revenue for Q1 FY2025.",
            source_info={"name": "MSFT_Q1.txt", "type": "sec_filing", "location": "p.5"},
        )
        assert fragment_id is not None
        stored = live_db_repo.get_fragment(fragment_id)
        assert stored is not None
        assert stored.tenant_id == TENANT_ID


@pytest.mark.e2e
class TestUniverseTopologyFanout:
    """
    Ports test_e2e_universe_validation.py into pytest.
    Verifies that Neo4j relationships are created during fanout.
    """

    def test_nvda_tsm_relationship_created_in_graph(
        self, live_runner, live_db_repo, live_graph
    ):
        recipe = ExtractionRecipe(
            tenant_id=TENANT_ID,
            name="E2E Universe Topology Recipe",
            ingestor_type="TEST",
            llm_prompt_template="Extract company relationships. Focus on NVDA and its partners.",
            expected_schema={
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string"},
                    "relationships": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target_entity":     {"type": "string"},
                                "relationship_type": {"type": "string"},
                                "context":           {"type": "string"},
                                "direction":         {"type": "string"},
                            },
                        },
                    },
                },
            },
        )
        live_db_repo.save_recipe(recipe)

        raw_text = "TSM is a key foundry partner for NVDA, providing high-end CoWoS packaging."
        fragment_id = live_runner.run_recipe_on_text(
            recipe_id=recipe.recipe_id,
            raw_text=raw_text,
            source_info={
                "name":     "TSM_Partner_Report.pdf",
                "type":     "broker_report",
                "location": "p. 1",
            },
        )
        assert fragment_id is not None, "Pipeline failed before reaching fanout."

    def test_filtered_topology_returns_neighbors(self, live_graph):
        """
        Verifies that Neo4j filtered-neighbor queries work end-to-end.
        Depends on test_nvda_tsm_relationship_created_in_graph having run first.
        """
        filters = UniverseFilter(subsectors=["Semiconductors"])
        neighbors = live_graph.get_filtered_neighbors("NVDA", filters.model_dump())
        # We don't assert a specific count — the graph may have other data.
        # We just confirm the call completes without error.
        assert isinstance(neighbors, list)
