from typing import Dict, Any
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.models.domain.extraction_recipe import ExtractionRecipe


def call_llm(recipe: ExtractionRecipe, raw_text: str, llm: LLMProvider) -> Dict[str, Any]:
    """
    Step 2: Send raw text to the LLM using the recipe's prompt template and
    expected schema, and return the raw structured JSON output.

    Changing the LLM provider or prompt strategy only touches this step.
    """
    prompt = (
        f"Extract financial intelligence from:\n\n{raw_text}\n\n"
        f"Logic: {recipe.llm_prompt_template}"
    )
    return llm.generate_structured_output(prompt=prompt, output_schema=recipe.expected_schema)
