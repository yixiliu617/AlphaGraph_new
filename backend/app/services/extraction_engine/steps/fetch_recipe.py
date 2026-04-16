import uuid
from backend.app.interfaces.db_repository import DBRepository
from backend.app.models.domain.extraction_recipe import ExtractionRecipe


def fetch_recipe(recipe_id: uuid.UUID, db: DBRepository) -> ExtractionRecipe:
    """
    Step 1: Load a validated ExtractionRecipe from the relational store.
    Raises ValueError if the recipe does not exist.
    """
    recipe = db.get_recipe(recipe_id)
    if not recipe:
        raise ValueError(f"ExtractionRecipe {recipe_id} not found.")
    return recipe
