from typing import Dict, Any
from backend.app.services.extraction_engine.validators import ExtractionValidator
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe

_validator = ExtractionValidator()


def validate_output(
    raw_llm_json: Dict[str, Any],
    recipe: ExtractionRecipe,
    source_info: Dict[str, str],
) -> DataFragment:
    """
    Step 3: Pass raw LLM JSON through the Pydantic V2 firewall.
    Returns a fully validated DataFragment or raises a ValidationError.

    Changing validation rules only touches ExtractionValidator (validators.py),
    not this step's interface.
    """
    return _validator.create_fragment(
        raw_llm_json=raw_llm_json,
        recipe=recipe,
        source_info=source_info,
    )
