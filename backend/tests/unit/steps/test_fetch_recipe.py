import uuid
import pytest

from backend.app.services.extraction_engine.steps.fetch_recipe import fetch_recipe


@pytest.mark.unit
class TestFetchRecipe:

    def test_returns_recipe_when_found(self, mock_db, sample_recipe):
        mock_db.get_recipe.return_value = sample_recipe

        result = fetch_recipe(sample_recipe.recipe_id, mock_db)

        assert result == sample_recipe
        mock_db.get_recipe.assert_called_once_with(sample_recipe.recipe_id)

    def test_raises_value_error_when_recipe_missing(self, mock_db):
        mock_db.get_recipe.return_value = None
        missing_id = uuid.uuid4()

        with pytest.raises(ValueError, match=str(missing_id)):
            fetch_recipe(missing_id, mock_db)

    def test_db_is_called_with_exact_id(self, mock_db, sample_recipe):
        mock_db.get_recipe.return_value = sample_recipe
        fetch_recipe(sample_recipe.recipe_id, mock_db)

        args, _ = mock_db.get_recipe.call_args
        assert args[0] == sample_recipe.recipe_id
