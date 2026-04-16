"""
Unit tests for broker report extraction using MASTER_FINANCIAL_RECIPE.

These tests exercise the parts of the pipeline that are specific to broker
reports: the full schema (relationships, catalysts, causal_impacts, metrics)
flowing through validate_output and fanout_to_graph.

No DB, LLM, vector, or graph connections required — all adapters are mocked.
Run with: pytest -m unit
"""

import pytest
from unittest.mock import MagicMock, call

from backend.app.core.master_recipe import MASTER_FINANCIAL_RECIPE
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.enums import SourceType
from backend.app.services.extraction_engine.steps.validate import validate_output
from backend.app.services.extraction_engine.steps.fanout import fanout_to_graph, fanout_to_ledger

TENANT_ID = "broker-report-test-tenant"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def master_recipe() -> ExtractionRecipe:
    """ExtractionRecipe using the production MASTER_FINANCIAL_RECIPE schema."""
    return ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="AlphaGraph Master Intelligence Extractor",
        ingestor_type=MASTER_FINANCIAL_RECIPE["ingestor_type"],
        llm_prompt_template="Extract financial intelligence from this broker report.",
        expected_schema=MASTER_FINANCIAL_RECIPE["expected_schema"],
    )


@pytest.fixture
def broker_source_info() -> dict:
    return {
        "name":     "GS_NVDA_Initiation_2025.pdf",
        "type":     "broker_report",
        "location": "p. 3",
    }


@pytest.fixture
def broker_llm_output() -> dict:
    """
    Realistic LLM output for a broker report initiating coverage on NVDA.
    Matches MASTER_FINANCIAL_RECIPE schema.
    """
    return {
        "raw_text": (
            "Goldman Sachs initiates NVIDIA (NVDA) with a Buy rating and $200 price target. "
            "Blackwell demand exceeds supply through 2025. TSM is the sole foundry partner."
        ),
        "entity_type": "company",
        "event_date": "2025-03-18",
        "summary": {
            "key_points": [
                "GS initiates NVDA Buy at $200 PT",
                "Blackwell demand exceeds supply through 2025",
            ],
            "supporting_evidence": [
                "Data center revenue accelerating",
                "Hyperscaler capex commitments robust",
            ],
        },
        "relationships": [
            {
                "target_entity": "TSM",
                "relationship_type": "supplier",
                "context": "Sole foundry for Blackwell GPU",
                "direction": "positive",
            },
            {
                "target_entity": "AMZN",
                "relationship_type": "customer",
                "context": "AWS major Blackwell cluster buyer",
                "direction": "positive",
            },
        ],
        "catalysts": [
            {
                "description": "Blackwell volume ramp exceeds street estimates",
                "date": "2025-06-01",
                "is_future": True,
                "impact_reason": "Gross margin expansion above 76%",
            }
        ],
        "causal_impacts": [
            {
                "factor": "AI infrastructure buildout",
                "outcome": "Data center revenue outperformance",
                "evidence_sentence": "Hyperscaler capex commitments remain robust.",
            }
        ],
        "extracted_metrics": {
            "revenue_guidance": 43_000_000_000,
            "gross_margin": 76.5,
            "price_target": 200.0,
        },
    }


# ---------------------------------------------------------------------------
# validate_output — Pydantic firewall with broker report schema
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBrokerReportValidation:

    def test_returns_data_fragment(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert isinstance(fragment, DataFragment)

    def test_source_type_is_broker_report(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert fragment.source_type == SourceType.BROKER_REPORT.value

    def test_source_name_preserved(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert fragment.source == "GS_NVDA_Initiation_2025.pdf"

    def test_exact_location_preserved(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert fragment.exact_location == "p. 3"

    def test_raw_text_extracted_into_content(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert "Goldman Sachs initiates NVIDIA" in fragment.content["raw_text"]

    def test_relationships_preserved_in_extracted_metrics(
        self, master_recipe, broker_source_info, broker_llm_output
    ):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        metrics = fragment.content["extracted_metrics"]
        assert "relationships" in metrics
        assert len(metrics["relationships"]) == 2

    def test_catalysts_preserved_in_extracted_metrics(
        self, master_recipe, broker_source_info, broker_llm_output
    ):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        metrics = fragment.content["extracted_metrics"]
        assert "catalysts" in metrics
        assert len(metrics["catalysts"]) == 1
        assert metrics["catalysts"][0]["description"] == "Blackwell volume ramp exceeds street estimates"

    def test_causal_impacts_preserved_in_extracted_metrics(
        self, master_recipe, broker_source_info, broker_llm_output
    ):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        metrics = fragment.content["extracted_metrics"]
        assert "causal_impacts" in metrics
        assert metrics["causal_impacts"][0]["factor"] == "AI infrastructure buildout"

    def test_numeric_metrics_preserved(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        nested_metrics = fragment.content["extracted_metrics"]["extracted_metrics"]
        assert nested_metrics["gross_margin"] == 76.5
        assert nested_metrics["price_target"] == 200.0

    def test_summary_preserved(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        summary = fragment.content["extracted_metrics"].get("summary", {})
        assert len(summary["key_points"]) == 2

    def test_lineage_contains_recipe_id(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert str(master_recipe.recipe_id) in fragment.lineage

    def test_tenant_id_from_recipe(self, master_recipe, broker_source_info, broker_llm_output):
        fragment = validate_output(broker_llm_output, master_recipe, broker_source_info)
        assert fragment.tenant_id == TENANT_ID


# ---------------------------------------------------------------------------
# fanout_to_graph — relationship extraction from broker report fragment
# ---------------------------------------------------------------------------

def _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output) -> DataFragment:
    return validate_output(broker_llm_output, master_recipe, broker_source_info)


@pytest.mark.unit
class TestBrokerReportGraphFanout:

    def test_two_relationships_create_two_graph_edges(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        fragment = _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output)
        fanout_to_graph(fragment, mock_graph_db)
        assert mock_graph_db.add_relationship.call_count == 2

    def test_supplier_relationship_type_passed_correctly(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        fragment = _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output)
        fanout_to_graph(fragment, mock_graph_db)
        calls = mock_graph_db.add_relationship.call_args_list
        rel_types = [c.kwargs["relationship_type"] for c in calls]
        assert "supplier" in rel_types

    def test_target_entities_passed_correctly(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        fragment = _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output)
        fanout_to_graph(fragment, mock_graph_db)
        calls = mock_graph_db.add_relationship.call_args_list
        targets = [c.kwargs["target_id"] for c in calls]
        assert "TSM" in targets
        assert "AMZN" in targets

    def test_metadata_includes_fragment_id(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        fragment = _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output)
        fanout_to_graph(fragment, mock_graph_db)
        for c in mock_graph_db.add_relationship.call_args_list:
            assert "fragment_id" in c.kwargs["metadata"]
            assert str(fragment.fragment_id) == c.kwargs["metadata"]["fragment_id"]

    def test_context_passed_in_metadata(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        fragment = _make_broker_fragment(master_recipe, broker_source_info, broker_llm_output)
        fanout_to_graph(fragment, mock_graph_db)
        first_call = mock_graph_db.add_relationship.call_args_list[0]
        assert "context" in first_call.kwargs["metadata"]


# ---------------------------------------------------------------------------
# Edge cases — missing or partial broker report LLM output
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBrokerReportEdgeCases:

    def test_missing_relationships_skips_graph(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        output = {**broker_llm_output, "relationships": []}
        fragment = validate_output(output, master_recipe, broker_source_info)
        fanout_to_graph(fragment, mock_graph_db)
        mock_graph_db.add_relationship.assert_not_called()

    def test_missing_catalysts_skips_ledger(
        self, master_recipe, broker_source_info, broker_llm_output, mock_db
    ):
        output = {k: v for k, v in broker_llm_output.items() if k != "catalysts"}
        fragment = validate_output(output, master_recipe, broker_source_info)
        fanout_to_ledger(fragment, mock_db)
        mock_db.get_ledger.assert_not_called()

    def test_no_raw_text_gets_fallback(
        self, master_recipe, broker_source_info, broker_llm_output
    ):
        output = {k: v for k, v in broker_llm_output.items() if k != "raw_text"}
        fragment = validate_output(output, master_recipe, broker_source_info)
        # Should not raise; raw_text gets a fallback value from the validator
        assert "raw_text" in fragment.content
        assert fragment.content["raw_text"] != ""

    def test_relationship_missing_type_gets_default(
        self, master_recipe, broker_source_info, broker_llm_output, mock_graph_db
    ):
        output = dict(broker_llm_output)
        output["relationships"] = [{"target_entity": "INTC"}]  # no relationship_type
        fragment = validate_output(output, master_recipe, broker_source_info)
        fanout_to_graph(fragment, mock_graph_db)
        rel_type = mock_graph_db.add_relationship.call_args.kwargs["relationship_type"]
        assert rel_type == "mentioned_with"
