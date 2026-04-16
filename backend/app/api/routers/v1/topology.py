from fastapi import APIRouter, Depends
from backend.app.models.api_contracts import APIResponse
from backend.app.api.dependencies import get_graph_repo
from backend.app.adapters.graph.neo4j_adapter import Neo4jAdapter

router = APIRouter()

from backend.app.models.domain.universe import UniverseFilter

@router.post("/filtered/{node_id}", response_model=APIResponse)
async def get_filtered_node_topology(
    node_id: str,
    filters: UniverseFilter,
    graph_db: Neo4jAdapter = Depends(get_graph_repo)
):
    """
    Retrieves neighbors filtered by GICS or User Categories.
    Powering the 'Focused Display' requirement.
    """
    try:
        neighbors = graph_db.get_filtered_neighbors(node_id, filters.model_dump())
        return APIResponse(success=True, data=neighbors)
    except Exception as e:
        return APIResponse(success=False, error=str(e))

@router.get("/path", response_model=APIResponse)
async def get_shortest_path(
    start_id: str,
    end_id: str,
    graph_db: Neo4jAdapter = Depends(get_graph_repo)
):
    """
    Finds the shortest relationship path between two financial entities.
    """
    try:
        path = graph_db.find_paths(start_id, end_id)
        return APIResponse(success=True, data=path)
    except Exception as e:
        return APIResponse(success=False, error=str(e))
