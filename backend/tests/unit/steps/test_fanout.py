import pytest
from unittest.mock import MagicMock

from backend.app.services.extraction_engine.steps.fanout import fanout_to_graph, fanout_to_ledger
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.enums import SourceType

TENANT_ID = "test-tenant-001"


def _make_fragment(extracted_metrics: dict) -> DataFragment:
    """Helper — builds a minimal DataFragment with the given extracted_metrics."""
    return DataFragment(
        tenant_id=TENANT_ID,
        source_type=SourceType.BROKER_REPORT,
        source="TSM_Partner_Report.pdf",
        exact_location="p. 1",
        reason_for_extraction="test",
        lineage=[],
        content={"raw_text": "test content", "extracted_metrics": extracted_metrics},
    )


# ---------------------------------------------------------------------------
# fanout_to_graph
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFanoutToGraph:

    def test_adds_relationship_for_each_entry(self, mock_graph_db):
        fragment = _make_fragment({
            "entity_name": "NVDA",
            "relationships": [
                {"target_entity": "TSM",  "relationship_type": "supplier", "context": "CoWoS", "direction": "inbound"},
                {"target_entity": "MSFT", "relationship_type": "customer", "context": "Azure",  "direction": "outbound"},
            ],
        })
        fanout_to_graph(fragment, mock_graph_db)
        assert mock_graph_db.add_relationship.call_count == 2

    def test_no_relationships_key_skips_graph(self, mock_graph_db):
        fragment = _make_fragment({"revenue": 50_000_000})
        fanout_to_graph(fragment, mock_graph_db)
        mock_graph_db.add_relationship.assert_not_called()

    def test_empty_relationships_list_skips_graph(self, mock_graph_db):
        fragment = _make_fragment({"entity_name": "NVDA", "relationships": []})
        fanout_to_graph(fragment, mock_graph_db)
        mock_graph_db.add_relationship.assert_not_called()

    def test_source_id_uses_entity_name_when_present(self, mock_graph_db):
        fragment = _make_fragment({
            "entity_name": "NVDA",
            "relationships": [{"target_entity": "TSM", "relationship_type": "supplier"}],
        })
        fanout_to_graph(fragment, mock_graph_db)
        source_id = mock_graph_db.add_relationship.call_args.kwargs["source_id"]
        assert source_id == "NVDA"

    def test_source_id_falls_back_to_fragment_source(self, mock_graph_db):
        """If entity_name absent, fragment.source is used as the graph node id."""
        fragment = _make_fragment({
            "relationships": [{"target_entity": "TSM", "relationship_type": "partner"}],
        })
        fanout_to_graph(fragment, mock_graph_db)
        source_id = mock_graph_db.add_relationship.call_args.kwargs["source_id"]
        assert source_id == fragment.source

    def test_relationship_metadata_contains_fragment_id(self, mock_graph_db):
        fragment = _make_fragment({
            "entity_name": "NVDA",
            "relationships": [{"target_entity": "TSM", "relationship_type": "supplier"}],
        })
        fanout_to_graph(fragment, mock_graph_db)
        metadata = mock_graph_db.add_relationship.call_args.kwargs["metadata"]
        assert metadata["fragment_id"] == str(fragment.fragment_id)

    def test_default_relationship_type_when_missing(self, mock_graph_db):
        fragment = _make_fragment({
            "entity_name": "NVDA",
            "relationships": [{"target_entity": "TSM"}],  # relationship_type absent
        })
        fanout_to_graph(fragment, mock_graph_db)
        rel_type = mock_graph_db.add_relationship.call_args.kwargs["relationship_type"]
        assert rel_type == "mentioned_with"


# ---------------------------------------------------------------------------
# fanout_to_ledger
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFanoutToLedger:

    def test_no_catalysts_never_calls_db(self, mock_db):
        fragment = _make_fragment({"revenue": 50_000_000})
        fanout_to_ledger(fragment, mock_db)
        mock_db.get_ledger.assert_not_called()

    def test_empty_catalysts_list_never_calls_db(self, mock_db):
        fragment = _make_fragment({"catalysts": []})
        fanout_to_ledger(fragment, mock_db)
        mock_db.get_ledger.assert_not_called()

    def test_catalysts_present_but_no_ledger_skips_update(self, mock_db):
        mock_db.get_ledger.return_value = None
        fragment = _make_fragment({"catalysts": [{"description": "Earnings beat expected"}]})
        fanout_to_ledger(fragment, mock_db)
        mock_db.update_ledger.assert_not_called()

    def test_catalysts_with_ledger_calls_update(self, mock_db):
        mock_db.get_ledger.return_value = MagicMock()
        fragment = _make_fragment({"catalysts": [{"description": "Revenue guidance raised"}]})
        fanout_to_ledger(fragment, mock_db)
        mock_db.update_ledger.assert_called_once()

    def test_get_ledger_called_with_tenant_id(self, mock_db):
        mock_db.get_ledger.return_value = None
        fragment = _make_fragment({"catalysts": [{"description": "Catalyst A"}]})
        fanout_to_ledger(fragment, mock_db)
        mock_db.get_ledger.assert_called_once_with(TENANT_ID)
