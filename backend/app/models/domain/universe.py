from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
import uuid

class PublicCompany(BaseModel):
    """
    Standardized company data from public sources (e.g. Compustat/CRSP).
    """
    ticker: str = Field(..., primary_key=True)
    name: str = Field(...)
    gics_sector: str = Field(..., description="e.g. Information Technology")
    gics_subsector: str = Field(..., description="e.g. Semiconductors")
    gics_subindustry: str = Field(..., description="e.g. SPE")
    
    metadata: Dict[str, Any] = Field(default_factory=dict)

class UserCompany(BaseModel):
    """
    Tenant-specific categorization for companies in their universe.
    """
    company_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str = Field(...)
    ticker: str = Field(...)
    user_category_1: Optional[str] = Field(None, description="e.g. AI Winners")
    user_category_2: Optional[str] = Field(None, description="e.g. High Conviction")
    
    is_active: bool = Field(default=True)

class UniverseFilter(BaseModel):
    """
    Payload for the Topology Tab to request a focused view.
    """
    sectors: List[str] = Field(default_factory=list)
    subsectors: List[str] = Field(default_factory=list)
    user_categories: List[str] = Field(default_factory=list)
    tickers: List[str] = Field(default_factory=list)
