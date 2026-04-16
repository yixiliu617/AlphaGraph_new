from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict
import uuid
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.thesis_ledger import ThesisLedger

class DBRepository(ABC):
    """
    PORT: Abstract Base Class for Relational and State storage (PostgreSQL).
    Ensures the business logic doesn't care about the DB implementation.
    """
    
    @abstractmethod
    def save_fragment(self, fragment: DataFragment) -> bool:
        pass

    @abstractmethod
    def get_fragment(self, fragment_id: uuid.UUID) -> Optional[DataFragment]:
        pass

    @abstractmethod
    def get_tenant_fragments(self, tenant_id: str, limit: int = 50) -> List[DataFragment]:
        pass

    @abstractmethod
    def save_recipe(self, recipe: ExtractionRecipe) -> bool:
        pass

    @abstractmethod
    def get_recipe(self, recipe_id: uuid.UUID) -> Optional[ExtractionRecipe]:
        pass

    @abstractmethod
    def get_ledger(self, tenant_id: str) -> Optional[ThesisLedger]:
        pass

    @abstractmethod
    def update_ledger(self, ledger: ThesisLedger) -> bool:
        pass

    # --- UNIVERSE MANAGEMENT ---
    @abstractmethod
    def get_public_companies(self, sector: Optional[str] = None) -> List[Any]:
        pass

    @abstractmethod
    def get_user_universe(self, tenant_id: str) -> List[Any]:
        pass

    @abstractmethod
    def save_public_company(self, company: Any) -> bool:
        pass
