"""
Integration tests for ExtractionRunner.

Uses a real in-memory SQLite database (via the sqlite_db_repo fixture in
integration/conftest.py) and mock adapters for the external services
(Gemini, Pinecone, Neo4j) — no API keys or live connections required.

These tests exercise the full pipeline code path, including actual ORM
writes and reads, so they catch issues that unit-level mocks would miss
(e.g. ORM mapping errors, field coercion, session lifecycle bugs).
"""

import uuid
import pytest
from unittest.mock import MagicMock

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository
from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.models.domain.extraction_recipe import ExtractionRecipe

TENANT_ID = "integration-test-tenant"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def llm_with_output() -> MagicMock:
    """Mock LLM that returns a realistic structured extraction result."""
    mock = MagicMock(spec=LLMProvider)
    mock.generate_structured_output.return_value = {
        "raw_text":     "Apple Q4 revenue was $119.6B, beating consensus by 3%.",
        "revenue":      119_600_000_000,
        "net_income":   29_900_000_000,
        "primary_risk": "Macroeconomic headwinds and FX pressure.",
    }
    mock.get_embeddings.return_value = [[0.1] * 768]
    return mock


@pytest.fixture
def runner(sqlite_db_repo, llm_with_output) -> ExtractionRunner:
    vector = MagicMock(spec=VectorRepository)
    vector.upsert_vectors.return_value = True
    graph = MagicMock(spec=GraphRepository)
    graph.add_relationship.return_value = True
    return ExtractionRunner(
        db=sqlite_db_repo,
        llm=llm_with_output,
        vector_db=vector,
        graph_db=graph,
    )


@pytest.fixture
def saved_recipe(sqlite_db_repo) -> ExtractionRecipe:
    """A recipe persisted to the in-memory DB before each test."""
    recipe = ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="Integration Test Recipe — SEC Revenue",
        ingestor_type="SEC_TEXT",
        llm_prompt_template="Extract revenue, net income, and primary risk factor.",
        expected_schema={
            "type": "object",
            "properties": {
                "revenue":      {"type": "number"},
                "net_income":   {"type": "number"},
                "primary_risk": {"type": "string"},
            },
        },
    )
    sqlite_db_repo.save_recipe(recipe)
    return recipe


SOURCE_INFO = {
    "name":     "AAPL_10K_2024.txt",
    "type":     "sec_filing",
    "location": "p. 42",
}

RAW_TEXT = "Apple Inc. filed its Q4 2024 10-K. Revenue of $119.6B exceeded analyst expectations."


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestExtractionRunnerIntegration:

    def test_returns_fragment_id_on_success(self, runner, saved_recipe):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        assert fragment_id is not None
        assert isinstance(fragment_id, uuid.UUID)

    def test_fragment_persisted_in_db(self, runner, saved_recipe, sqlite_db_repo):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        stored = sqlite_db_repo.get_fragment(fragment_id)
        assert stored is not None

    def test_persisted_fragment_has_correct_tenant(
        self, runner, saved_recipe, sqlite_db_repo
    ):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        stored = sqlite_db_repo.get_fragment(fragment_id)
        assert stored.tenant_id == TENANT_ID

    def test_persisted_fragment_lineage_contains_recipe_id(
        self, runner, saved_recipe, sqlite_db_repo
    ):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        stored = sqlite_db_repo.get_fragment(fragment_id)
        assert str(saved_recipe.recipe_id) in stored.lineage

    def test_persisted_fragment_content_has_raw_text(
        self, runner, saved_recipe, sqlite_db_repo
    ):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        stored = sqlite_db_repo.get_fragment(fragment_id)
        assert "raw_text" in stored.content

    def test_returns_none_for_missing_recipe(self, runner):
        result = runner.run_recipe_on_text(
            recipe_id=uuid.uuid4(),   # does not exist in DB
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        assert result is None

    def test_two_sequential_runs_produce_distinct_fragment_ids(
        self, runner, saved_recipe
    ):
        id_1 = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text="First document.",
            source_info=SOURCE_INFO,
        )
        id_2 = runner.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text="Second document.",
            source_info=SOURCE_INFO,
        )
        assert id_1 != id_2

    def test_vector_upsert_called_after_db_save(
        self, sqlite_db_repo, llm_with_output, saved_recipe
    ):
        """DB write must precede vector upsert — verifies step 4 ordering."""
        call_order = []
        vector = MagicMock(spec=VectorRepository)
        graph  = MagicMock(spec=GraphRepository)

        # Patch save_fragment to record order
        original_save = sqlite_db_repo.save_fragment
        def tracked_save(fragment):
            call_order.append("db")
            return original_save(fragment)

        sqlite_db_repo.save_fragment = tracked_save
        vector.upsert_vectors.side_effect = lambda **kw: call_order.append("vector")

        r = ExtractionRunner(
            db=sqlite_db_repo, llm=llm_with_output, vector_db=vector, graph_db=graph
        )
        r.run_recipe_on_text(
            recipe_id=saved_recipe.recipe_id,
            raw_text=RAW_TEXT,
            source_info=SOURCE_INFO,
        )
        assert call_order == ["db", "vector"]
