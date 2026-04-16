from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime
import uuid
from backend.app.models.domain.enums import TenantTier, SourceType

class DataFragment(BaseModel):
    """
    Institutional Standard: Combines strict Enums, granular lineage,
    and dynamic JSON validation for extracted metrics.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True
    )

    fragment_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str = Field(..., description="Unique ID for the tenant/PM")
    tenant_tier: TenantTier = Field(default=TenantTier.PRIVATE)
    
    lineage: List[str] = Field(default_factory=list, description="Audit trail of extraction recipe IDs")
    source_type: SourceType = Field(..., description="Categorization for downstream routing")
    source: str = Field(..., description="The document name or URI")
    exact_location: str = Field(..., description="Page number, paragraph index, or XBRL tag")
    
    reason_for_extraction: str = Field(..., description="Context for why the agent pulled this")
    
    # content structure optimized for dynamic extraction
    content: Dict[str, Any] = Field(
        ..., 
        description="Must contain 'raw_text' and 'extracted_metrics'. 'extracted_metrics' accepts dynamic JSON."
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator('content')
    @classmethod
    def validate_content_structure(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if "raw_text" not in v:
            raise ValueError("content must contain 'raw_text'")
        if "extracted_metrics" not in v:
            v["extracted_metrics"] = {}
        return v
