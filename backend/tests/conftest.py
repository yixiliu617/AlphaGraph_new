"""
Shared fixtures available to every test tier (unit, integration, e2e).

Fixtures are grouped by what they provide:
  - Sample domain objects  (recipe, source_info, raw LLM JSON, fragment)
  - Mock adapters          (db, llm, vector_db, graph_db, quant)

Each fixture is intentionally minimal — tests that need custom behaviour
should override the return value directly on the mock.
"""

import pytest
from unittest.mock import MagicMock

from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository
from backend.app.interfaces.quant_repository import QuantRepository
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.enums import SourceType

TENANT_ID = "test-tenant-001"

# ---------------------------------------------------------------------------
# Sample domain objects
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_recipe() -> ExtractionRecipe:
    return ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="Test SEC Revenue Extractor",
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


@pytest.fixture
def sample_source_info() -> dict:
    return {
        "name":     "AAPL_10K_2024.txt",
        "type":     "sec_filing",
        "location": "p. 42",
    }


@pytest.fixture
def sample_raw_llm_json() -> dict:
    """
    Represents the raw JSON dict returned by the LLM in step 2 (call_llm).
    'raw_text' is the only reserved key; everything else lands in extracted_metrics.
    """
    return {
        "raw_text":     "Apple reported Q4 revenue of $119.6B, net income $29.9B.",
        "revenue":      119_600_000_000,
        "net_income":   29_900_000_000,
        "primary_risk": "Macroeconomic headwinds and FX pressure.",
    }


@pytest.fixture
def sample_fragment(sample_recipe, sample_source_info, sample_raw_llm_json) -> DataFragment:
    """A pre-validated DataFragment ready for storage/fanout tests."""
    metrics = {k: v for k, v in sample_raw_llm_json.items() if k != "raw_text"}
    return DataFragment(
        tenant_id=TENANT_ID,
        lineage=[str(sample_recipe.recipe_id)],
        source_type=SourceType.SEC_FILING,
        source=sample_source_info["name"],
        exact_location=sample_source_info["location"],
        reason_for_extraction=f"Extracted via recipe: {sample_recipe.name}",
        content={
            "raw_text":          sample_raw_llm_json["raw_text"],
            "extracted_metrics": metrics,
        },
    )


# ---------------------------------------------------------------------------
# Mock adapters — spec= catches wrong method calls at test time
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db() -> MagicMock:
    mock = MagicMock(spec=DBRepository)
    mock.save_fragment.return_value = True
    mock.save_recipe.return_value   = True
    mock.get_ledger.return_value    = None   # no ledger by default
    return mock


@pytest.fixture
def mock_llm() -> MagicMock:
    mock = MagicMock(spec=LLMProvider)
    mock.get_embeddings.return_value    = [[0.1] * 768]
    mock.generate_response.return_value = "Generic financial analysis response."
    return mock


@pytest.fixture
def mock_vector_db() -> MagicMock:
    mock = MagicMock(spec=VectorRepository)
    mock.upsert_vectors.return_value = True
    return mock


@pytest.fixture
def mock_graph_db() -> MagicMock:
    mock = MagicMock(spec=GraphRepository)
    mock.add_relationship.return_value = True
    return mock


@pytest.fixture
def mock_quant() -> MagicMock:
    mock = MagicMock(spec=QuantRepository)
    mock.execute_query.return_value = [{"revenue": 119_600_000_000, "period": "Q4 2024"}]
    return mock
