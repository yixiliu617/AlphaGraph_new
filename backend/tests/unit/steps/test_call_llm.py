import pytest

from backend.app.services.extraction_engine.steps.call_llm import call_llm


@pytest.mark.unit
class TestCallLLM:

    def test_returns_dict_from_llm(self, mock_llm, sample_recipe, sample_raw_llm_json):
        mock_llm.generate_structured_output.return_value = sample_raw_llm_json

        result = call_llm(sample_recipe, "Apple Q4 earnings...", mock_llm)

        assert result == sample_raw_llm_json

    def test_prompt_contains_raw_text(self, mock_llm, sample_recipe, sample_raw_llm_json):
        mock_llm.generate_structured_output.return_value = sample_raw_llm_json
        sentinel = "UNIQUE_SENTINEL_TEXT_XYZ"

        call_llm(sample_recipe, sentinel, mock_llm)

        prompt = mock_llm.generate_structured_output.call_args.kwargs["prompt"]
        assert sentinel in prompt

    def test_prompt_contains_recipe_template(self, mock_llm, sample_recipe, sample_raw_llm_json):
        mock_llm.generate_structured_output.return_value = sample_raw_llm_json

        call_llm(sample_recipe, "some text", mock_llm)

        prompt = mock_llm.generate_structured_output.call_args.kwargs["prompt"]
        assert sample_recipe.llm_prompt_template in prompt

    def test_passes_expected_schema_to_llm(self, mock_llm, sample_recipe, sample_raw_llm_json):
        mock_llm.generate_structured_output.return_value = sample_raw_llm_json

        call_llm(sample_recipe, "some text", mock_llm)

        schema = mock_llm.generate_structured_output.call_args.kwargs["output_schema"]
        assert schema == sample_recipe.expected_schema

    def test_llm_called_exactly_once(self, mock_llm, sample_recipe, sample_raw_llm_json):
        mock_llm.generate_structured_output.return_value = sample_raw_llm_json

        call_llm(sample_recipe, "some text", mock_llm)

        mock_llm.generate_structured_output.assert_called_once()
