from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
import uuid
from backend.app.models.domain.enums import PositionSide, CatalystStatus, AssetClass

class Catalyst(BaseModel):
    """
    Specific event or metric that triggers a re-evaluation of a thesis.
    """
    catalyst_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    description: str = Field(..., description="What event must occur?")
    status: CatalystStatus = Field(default=CatalystStatus.PENDING)
    impact_weight: float = Field(default=1.0, description="1-10 scale of importance")
    
    # Linked Data_Fragments that support or trigger this catalyst
    supporting_fragment_ids: List[uuid.UUID] = Field(default_factory=list)
    
    triggered_at: Optional[datetime] = None

class ThesisPosition(BaseModel):
    """
    An active Long or Short position linked to a thesis.
    """
    position_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    ticker: str = Field(..., description="The symbol/identifier")
    side: PositionSide = Field(...)
    asset_class: AssetClass = Field(default=AssetClass.EQUITY)
    
    summary: str = Field(..., description="High-level summary of the bull/bear case")
    catalysts: List[Catalyst] = Field(default_factory=list)
    
    # Quantitative thresholds (e.g., target price)
    targets: Dict[str, float] = Field(default_factory=dict)
    
    is_active: bool = Field(default=True)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

class ThesisLedger(BaseModel):
    """
    The 'THESIS.md' equivalent: A collection of active research positions per tenant.
    Ambient agents evaluate new fragments against this ledger.
    """
    model_config = ConfigDict(use_enum_values=True)

    ledger_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str = Field(...)
    
    positions: List[ThesisPosition] = Field(default_factory=list)
    
    # High-level portfolio metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
