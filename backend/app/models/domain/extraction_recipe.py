from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
import uuid

class ExtractionRecipe(BaseModel):
    """
    Combined Institutional Standard: Supports versioning, targeting, and
    shareable JSON Schemas for dynamic LLM-driven extractions.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "recipe_id": "8a3b2c1d-4e5f-6g7h-8i9j-0k1l2m3n4o5p",
                "tenant_id": "tenant-123",
                "name": "SEC 10-K Revenue Extractor",
                "target_sectors": ["Technology", "Software"],
                "version": 1,
                "ingestor_type": "SEC_XBRL_Parser",
                "expected_schema": {
                    "type": "object",
                    "properties": {
                        "revenue": {"type": "number"},
                        "ebitda": {"type": "number"}
                    }
                }
            }
        }
    )

    recipe_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str = Field(...)
    name: str = Field(..., description="The label for the PM's strategy")
    
    target_sectors: List[str] = Field(
        default_factory=list, 
        description="Used for library discovery and filtering"
    )
    
    version: int = Field(default=1, description="Sequential versioning for immutability")
    ingestor_type: str = Field(..., description="Determines which loader (PDF/SEC/API) is used")
    
    # User's logic as data
    llm_prompt_template: str = Field(..., description="The PM's custom logic instructions")
    
    # Stored as RAW JSON SCHEMA to pass directly to LLMs
    expected_schema: Dict[str, Any] = Field(
        ..., 
        description="The dynamic JSON contract for the LLM to fill."
    )
    
    is_public: bool = Field(default=False, description="Whether this strategy is shared")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def to_dict(self):
        return self.model_dump()
