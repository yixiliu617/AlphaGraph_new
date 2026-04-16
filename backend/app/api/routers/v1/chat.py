from fastapi import APIRouter, Depends
from backend.app.models.api_contracts import ChatRequest, ChatResponse, APIResponse
from backend.app.api.dependencies import get_engine_agent
from backend.app.agents.router_agent import EngineAgent

router = APIRouter()


@router.post("/", response_model=APIResponse)
async def unified_chat(
    request: ChatRequest,
    agent: EngineAgent = Depends(get_engine_agent),
):
    """
    Main entrypoint for the Unified Data Engine.

    Routes queries via Claude tool-use to:
      - get_financial_data -> DataAgentExecutor -> topline/calculated parquets
      - search_documents   -> PineconeExecutor  -> semantic document index

    Returns synthesis text + structured AgentResponseBlocks for the frontend.
    """
    try:
        response: ChatResponse = agent.process_query(
            message=request.message,
            session_id=request.session_id,
        )
        return APIResponse(success=True, data=response)
    except Exception as e:
        return APIResponse(success=False, error=str(e))
