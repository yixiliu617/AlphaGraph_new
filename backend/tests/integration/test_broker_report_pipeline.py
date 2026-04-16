"""
Integration test: full broker report extraction pipeline.

Runs the complete ExtractionRunner.run_recipe_on_text() pipeline against an
in-memory SQLite DB using MASTER_FINANCIAL_RECIPE as the schema.

The LLM is mocked to return broker-report-shaped structured output.
Graph, vector, and ledger adapters are mocked so no external connections needed.

Run with: pytest -m integration
"""

import uuid
import pytest
from unittest.mock import MagicMock

from backend.app.core.master_recipe import MASTER_FINANCIAL_RECIPE
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.enums import SourceType
from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository

TENANT_ID = "broker-integration-test-tenant"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def broker_recipe(sqlite_db_repo) -> ExtractionRecipe:
    """
    Seed MASTER_FINANCIAL_RECIPE into SQLite so fetch_recipe() can load it.
    Returns the saved ExtractionRecipe with its recipe_id.
    """
    recipe = ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="AlphaGraph Master Intelligence Extractor",
        ingestor_type=MASTER_FINANCIAL_RECIPE["ingestor_type"],
        llm_prompt_template="Extract financial intelligence from this broker report.",
        expected_schema=MASTER_FINANCIAL_RECIPE["expected_schema"],
    )
    sqlite_db_repo.save_recipe(recipe)
    return recipe


@pytest.fixture
def broker_llm_output() -> dict:
    """
    Realistic structured output a LLM would return for a broker-report chunk.
    Matches MASTER_FINANCIAL_RECIPE schema exactly.
    """
    return {
        "raw_text": (
            "Morgan Stanley initiates AMD with Overweight and $180 price target. "
            "AI accelerator TAM exceeds $400B by 2027. TSMC is primary foundry partner."
        ),
        "entity_type": "company",
        "event_date": "2025-04-01",
        "summary": {
            "key_points": [
                "MS initiates AMD Overweight at $180 PT",
                "AI accelerator TAM $400B+ by 2027",
            ],
            "supporting_evidence": [
                "MI300X gaining hyperscaler adoption",
                "AMD server GPU attach rate rising",
            ],
        },
        "relationships": [
            {
                "target_entity": "TSMC",
                "relationship_type": "supplier",
                "context": "Primary foundry for MI300X production",
                "direction": "positive",
            },
            {
                "target_entity": "MSFT",
                "relationship_type": "customer",
                "context": "Azure AI compute includes MI300X deployments",
                "direction": "positive",
            },
            {
                "target_entity": "NVDA",
                "relationship_type": "competitor",
                "context": "Direct H100/H200 competitor in data center GPU",
                "direction": "neutral",
            },
        ],
        "catalysts": [
            {
                "description": "MI300X volume ramp in hyperscaler AI clusters",
                "date": "2025-07-01",
                "is_future": True,
                "impact_reason": "Revenue diversification away from gaming",
            },
            {
                "description": "Data center GPU revenue exceeds gaming for first time",
                "date": "2025-03-31",
                "is_future": False,
                "impact_reason": "Structural shift in AMD revenue mix",
            },
        ],
        "causal_impacts": [
            {
                "factor": "AI infrastructure buildout by hyperscalers",
                "outcome": "AMD data center GPU TAM expansion",
                "evidence_sentence": "Microsoft, Google, and Amazon are all evaluating MI300X at scale.",
            }
        ],
        "extracted_metrics": {
            "price_target": 180.0,
            "gross_margin": 53.0,
            "revenue_guidance": 7_700_000_000,
        },
    }


@pytest.fixture
def mock_llm_broker(broker_llm_output) -> MagicMock:
    """
    LLM mock that returns broker-report-shaped structured output.
    Embeddings return a fixed 768-dim vector.
    """
    mock = MagicMock()
    mock.generate_structured_output.return_value = broker_llm_output
    mock.get_embeddings.return_value = [[0.1] * 768]
    return mock


@pytest.fixture
def mock_vector() -> MagicMock:
    mock = MagicMock(spec=VectorRepository)
    mock.upsert_vectors.return_value = True
    return mock


@pytest.fixture
def mock_graph() -> MagicMock:
    mock = MagicMock(spec=GraphRepository)
    mock.add_relationship.return_value = True
    return mock


@pytest.fixture
def runner(sqlite_db_repo, mock_llm_broker, mock_vector, mock_graph) -> ExtractionRunner:
    return ExtractionRunner(
        db=sqlite_db_repo,
        llm=mock_llm_broker,
        vector_db=mock_vector,
        graph_db=mock_graph,
    )


@pytest.fixture
def broker_source_info() -> dict:
    return {
        "name":     "MS_AMD_Initiation_2025.pdf",
        "type":     "broker_report",
        "location": "p. 5",
    }


# ---------------------------------------------------------------------------
# Helper: run the pipeline once and retrieve the persisted fragment
# ---------------------------------------------------------------------------

def _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo):
    raw_text = (
        "Morgan Stanley initiates AMD with Overweight and $180 price target. "
        "AI accelerator TAM exceeds $400B by 2027."
    )
    fragment_id = runner.run_recipe_on_text(
        recipe_id=broker_recipe.recipe_id,
        raw_text=raw_text,
        source_info=broker_source_info,
    )
    assert fragment_id is not None, "Pipeline returned None — check runner error log"
    fragment = sqlite_db_repo.get_fragment(fragment_id)
    assert fragment is not None, "Fragment not found in DB after pipeline run"
    return fragment


# ---------------------------------------------------------------------------
# Full pipeline smoke test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestBrokerReportPipelineSmoke:

    def test_pipeline_returns_fragment_id(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        raw_text = "Morgan Stanley initiates AMD with Overweight and $180 PT."
        fragment_id = runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text=raw_text,
            source_info=broker_source_info,
        )
        assert fragment_id is not None
        assert isinstance(fragment_id, uuid.UUID)

    def test_fragment_persisted_to_db(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert fragment is not None

    def test_source_type_is_broker_report(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert fragment.source_type == SourceType.BROKER_REPORT.value

    def test_source_name_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert fragment.source == "MS_AMD_Initiation_2025.pdf"

    def test_exact_location_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert fragment.exact_location == "p. 5"

    def test_tenant_id_set_from_recipe(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert fragment.tenant_id == TENANT_ID

    def test_lineage_contains_recipe_id(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert str(broker_recipe.recipe_id) in fragment.lineage


# ---------------------------------------------------------------------------
# Content: structured fields preserved through the pipeline
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestBrokerReportContentPreservation:

    def test_raw_text_in_content(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        assert "raw_text" in fragment.content
        assert fragment.content["raw_text"] != ""

    def test_three_relationships_in_extracted_metrics(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        metrics = fragment.content["extracted_metrics"]
        assert "relationships" in metrics
        assert len(metrics["relationships"]) == 3

    def test_relationship_types_correct(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        rel_types = {
            r["relationship_type"]
            for r in fragment.content["extracted_metrics"]["relationships"]
        }
        assert "supplier" in rel_types
        assert "customer" in rel_types
        assert "competitor" in rel_types

    def test_target_entities_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        targets = {
            r["target_entity"]
            for r in fragment.content["extracted_metrics"]["relationships"]
        }
        assert "TSMC" in targets
        assert "MSFT" in targets
        assert "NVDA" in targets

    def test_two_catalysts_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        metrics = fragment.content["extracted_metrics"]
        assert "catalysts" in metrics
        assert len(metrics["catalysts"]) == 2

    def test_causal_impact_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        metrics = fragment.content["extracted_metrics"]
        assert "causal_impacts" in metrics
        assert metrics["causal_impacts"][0]["factor"] == "AI infrastructure buildout by hyperscalers"

    def test_numeric_metrics_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        nested = fragment.content["extracted_metrics"]["extracted_metrics"]
        assert nested["price_target"] == 180.0
        assert nested["gross_margin"] == 53.0

    def test_summary_key_points_preserved(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo
    ):
        fragment = _run_and_fetch(runner, broker_recipe, broker_source_info, sqlite_db_repo)
        summary = fragment.content["extracted_metrics"].get("summary", {})
        assert len(summary["key_points"]) == 2


# ---------------------------------------------------------------------------
# Fanout: relationships drive graph edges
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestBrokerReportGraphFanout:

    def test_three_relationships_create_three_graph_edges(
        self, runner, broker_recipe, broker_source_info, mock_graph
    ):
        raw_text = "Morgan Stanley initiates AMD."
        runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text=raw_text,
            source_info=broker_source_info,
        )
        assert mock_graph.add_relationship.call_count == 3

    def test_supplier_relationship_type_passed_to_graph(
        self, runner, broker_recipe, broker_source_info, mock_graph
    ):
        runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text="Morgan Stanley initiates AMD.",
            source_info=broker_source_info,
        )
        rel_types = [
            c.kwargs["relationship_type"]
            for c in mock_graph.add_relationship.call_args_list
        ]
        assert "supplier" in rel_types

    def test_tsmc_passed_as_target_to_graph(
        self, runner, broker_recipe, broker_source_info, mock_graph
    ):
        runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text="Morgan Stanley initiates AMD.",
            source_info=broker_source_info,
        )
        targets = [
            c.kwargs["target_id"]
            for c in mock_graph.add_relationship.call_args_list
        ]
        assert "TSMC" in targets

    def test_graph_edge_metadata_has_fragment_id(
        self, runner, broker_recipe, broker_source_info, sqlite_db_repo, mock_graph
    ):
        fragment_id = runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text="Morgan Stanley initiates AMD.",
            source_info=broker_source_info,
        )
        for c in mock_graph.add_relationship.call_args_list:
            assert "fragment_id" in c.kwargs["metadata"]
            assert str(fragment_id) == c.kwargs["metadata"]["fragment_id"]

    def test_vector_upsert_called_once(
        self, runner, broker_recipe, broker_source_info, mock_vector
    ):
        runner.run_recipe_on_text(
            recipe_id=broker_recipe.recipe_id,
            raw_text="Morgan Stanley initiates AMD.",
            source_info=broker_source_info,
        )
        mock_vector.upsert_vectors.assert_called_once()
