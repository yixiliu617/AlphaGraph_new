from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
import uuid
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.thesis_ledger import ThesisLedger, ThesisPosition

# --- REQUEST CONTRACTS ---

class ChatRequest(BaseModel):
    """
    User query for the Unified Data Engine Tab.
    """
    message: str = Field(..., description="The natural language question")
    session_id: Optional[str] = Field(None, description="For multi-turn memory")
    context_filters: Optional[Dict[str, Any]] = Field(None, description="Limit search to specific tickers or sectors")

class IngestionRequest(BaseModel):
    """
    Trigger for a background extraction job.
    """
    source_uri: str = Field(..., description="Where the raw data is (S3, local, API)")
    recipe_id: uuid.UUID = Field(..., description="Which Extraction Recipe to use")
    raw_text: Optional[str] = Field(None, description="Direct text input for testing")

# --- RESPONSE CONTRACTS ---

class AgentResponseBlock(BaseModel):
    """
    Modular block returned to the frontend (Tab 2).
    """
    block_type: str = Field(..., description="chart, text, table, or graph")
    title: str = Field(..., description="Title for the UI card")
    data: Any = Field(..., description="The generic prop-ready payload for the dumb UI component")
    supporting_fragments: List[DataFragment] = Field(default_factory=list, description="For source verification")

class ChatResponse(BaseModel):
    """
    Synthesis response from the LangChain agent.
    """
    answer: str = Field(..., description="The main textual synthesis")
    blocks: List[AgentResponseBlock] = Field(default_factory=list, description="The draggable UI blocks")
    session_id: str = Field(...)

class LedgerUpdate(BaseModel):
    """
    WebSocket payload for real-time thesis catalyst alerts.
    """
    type: str = Field("catalyst_alert")
    position_id: uuid.UUID = Field(...)
    ticker: str = Field(...)
    fragment: DataFragment = Field(..., description="The evidence that triggered the alert")
    impact_description: str = Field(...)

# --- GENERIC WRAPPER ---

class APIResponse(BaseModel):
    """
    Standard wrapper for all API responses to ensure consistency.
    """
    success: bool = Field(default=True)
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
