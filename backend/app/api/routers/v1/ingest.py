from fastapi import APIRouter, Depends, HTTPException
from backend.app.models.api_contracts import IngestionRequest, APIResponse
from backend.app.api.dependencies import get_extraction_runner
from backend.app.services.extraction_engine.runner import ExtractionRunner

router = APIRouter()

@router.post("/", response_model=APIResponse)
async def trigger_ingestion(
    request: IngestionRequest,
    runner: ExtractionRunner = Depends(get_extraction_runner)
):
    """
    Endpoint to trigger a background extraction job.
    """
    if not request.raw_text:
        # In a real app, we'd fetch from source_uri here
        raise HTTPException(status_code=400, detail="raw_text is required for now.")

    source_info = {
        "name": request.source_uri,
        "type": "manual_upload",
        "location": "unknown"
    }

    fragment_id = runner.run_recipe_on_text(
        recipe_id=request.recipe_id,
        raw_text=request.raw_text,
        source_info=source_info
    )

    if not fragment_id:
        return APIResponse(success=False, error="Extraction failed.")

    return APIResponse(success=True, data={"fragment_id": str(fragment_id)})
