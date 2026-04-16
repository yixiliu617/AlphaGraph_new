import pytest

from backend.app.services.extraction_engine.steps.validate import validate_output
from backend.app.models.domain.data_fragment import DataFragment


@pytest.mark.unit
class TestValidateOutput:

    def test_returns_data_fragment_on_valid_input(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        result = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert isinstance(result, DataFragment)

    def test_tenant_id_flows_from_recipe(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert fragment.tenant_id == sample_recipe.tenant_id

    def test_raw_text_preserved_in_content(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert fragment.content["raw_text"] == sample_raw_llm_json["raw_text"]

    def test_non_raw_text_fields_go_to_extracted_metrics(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        metrics = fragment.content["extracted_metrics"]
        assert metrics["revenue"] == sample_raw_llm_json["revenue"]
        assert "raw_text" not in metrics

    def test_extracted_metrics_present_even_when_empty(
        self, sample_recipe, sample_source_info
    ):
        minimal_json = {"raw_text": "Only text, no metrics."}
        fragment = validate_output(minimal_json, sample_recipe, sample_source_info)
        assert "extracted_metrics" in fragment.content

    def test_lineage_contains_recipe_id(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert str(sample_recipe.recipe_id) in fragment.lineage

    def test_source_name_flows_from_source_info(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert fragment.source == sample_source_info["name"]

    def test_exact_location_flows_from_source_info(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        fragment = validate_output(sample_raw_llm_json, sample_recipe, sample_source_info)
        assert fragment.exact_location == sample_source_info["location"]

    def test_raises_when_raw_text_missing(self, sample_recipe, sample_source_info):
        bad_json = {"revenue": 50_000_000}  # raw_text omitted
        with pytest.raises(Exception):       # Pydantic ValidationError
            validate_output(bad_json, sample_recipe, sample_source_info)

    def test_fallback_raw_text_when_llm_omits_it(
        self, sample_recipe, sample_source_info, sample_raw_llm_json
    ):
        """
        ExtractionValidator inserts a fallback message when raw_text is absent
        instead of raising — verify the fragment is still valid.
        """
        json_without_raw_text = {k: v for k, v in sample_raw_llm_json.items() if k != "raw_text"}
        # validators.py sets a default string, so this should NOT raise
        fragment = validate_output(json_without_raw_text, sample_recipe, sample_source_info)
        assert "raw_text" in fragment.content
