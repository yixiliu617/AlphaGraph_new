from fastapi import APIRouter, Depends
from backend.app.models.api_contracts import APIResponse
from backend.app.api.dependencies import get_db_repo
from backend.app.adapters.db.postgres_adapter import PostgresAdapter

router = APIRouter()

@router.get("/{tenant_id}", response_model=APIResponse)
async def get_tenant_ledger(
    tenant_id: str,
    db: PostgresAdapter = Depends(get_db_repo)
):
    """
    Retrieves the full Thesis Ledger (THESIS.md state) for a tenant.
    """
    ledger = db.get_ledger(tenant_id)
    if not ledger:
        return APIResponse(success=False, error="Ledger not found.")
    return APIResponse(success=True, data=ledger)
