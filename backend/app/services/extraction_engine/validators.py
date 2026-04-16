from typing import Dict, Any, Optional
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
import uuid

class ExtractionValidator:
    """
    The 'Firewall' Logic: Validates that raw LLM JSON output 
    correctly maps into our strict DataFragment Pydantic model.
    """
    
    @staticmethod
    def create_fragment(
        raw_llm_json: Dict[str, Any], 
        recipe: ExtractionRecipe, 
        source_info: Dict[str, str]
    ) -> DataFragment:
        """
        Synthesizes raw LLM output and metadata into a validated DataFragment.
        Ensures the 'content' contains 'raw_text' and 'extracted_metrics'.
        """
        # 1. Extract raw_text if present, else fallback
        raw_text = raw_llm_json.get("raw_text", "Text content not provided by extractor.")
        
        # 2. Map all other LLM output fields into extracted_metrics
        # This allows the dynamic 'expected_schema' from the recipe to be stored safely.
        extracted_metrics = {k: v for k, v in raw_llm_json.items() if k != "raw_text"}
        
        # 3. Build the validated Pydantic model (The Firewall)
        fragment = DataFragment(
            tenant_id=recipe.tenant_id,
            lineage=[str(recipe.recipe_id)],
            source_type=source_info.get("type", "unknown"),
            source=source_info.get("name", "unknown_source"),
            exact_location=source_info.get("location", "unknown_location"),
            reason_for_extraction=f"Extracted via recipe: {recipe.name}",
            content={
                "raw_text": raw_text,
                "extracted_metrics": extracted_metrics
            }
        )
        
        return fragment
