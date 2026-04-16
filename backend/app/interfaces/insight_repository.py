"""
InsightRepository port.

Deliberately separate from DBRepository so the insights module has
zero coupling to the existing data layer interface. You can delete the
entire insights feature by removing this file and its adapter without
touching DBRepository or any other interface.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import uuid

from backend.app.models.domain.insight_models import InsightTemplate, InsightOutput


class InsightRepository(ABC):
    """
    PORT: Abstract contract for insight persistence.
    Covers InsightTemplate CRUD and InsightOutput lifecycle.
    """

    # -----------------------------------------------------------------------
    # InsightTemplate
    # -----------------------------------------------------------------------

    @abstractmethod
    def save_template(self, template: InsightTemplate) -> bool:
        """Upsert an InsightTemplate. Used for both create and update."""

    @abstractmethod
    def get_template(self, template_id: uuid.UUID) -> Optional[InsightTemplate]:
        """Return a single template by id, or None if not found."""

    @abstractmethod
    def list_templates(self, tenant_id: str) -> List[InsightTemplate]:
        """
        Return all templates visible to the given tenant:
        their own private templates + all public templates.
        NOTE: system (code-level) templates are merged in the service layer,
        not here. This method only covers DB-persisted templates.
        """

    @abstractmethod
    def delete_template(self, template_id: uuid.UUID, tenant_id: str) -> bool:
        """
        Delete a private template owned by tenant_id.
        Returns False if the template does not exist or is not owned by tenant.
        """

    # -----------------------------------------------------------------------
    # InsightOutput
    # -----------------------------------------------------------------------

    @abstractmethod
    def save_output(self, output: InsightOutput) -> bool:
        """Insert or replace an InsightOutput record."""

    @abstractmethod
    def get_output(self, insight_id: uuid.UUID) -> Optional[InsightOutput]:
        """Return a single InsightOutput by id, or None."""

    @abstractmethod
    def get_outputs_by_entity(
        self, tenant_id: str, entity: str, limit: int = 10
    ) -> List[InsightOutput]:
        """Return the most recent InsightOutputs that include the given ticker."""

    @abstractmethod
    def get_outputs_by_template(
        self, tenant_id: str, template_id: uuid.UUID, limit: int = 10
    ) -> List[InsightOutput]:
        """Return recent outputs produced from a specific template."""

    @abstractmethod
    def update_output_rating(
        self,
        insight_id: uuid.UUID,
        rating: str,
        edits: Optional[str] = None,
    ) -> bool:
        """Record the user's approve / edit / reject feedback on an insight."""

    @abstractmethod
    def find_cached_insight(
        self,
        tenant_id: str,
        template_id: uuid.UUID,
        entities: List[str],
        staleness_days: int,
    ) -> Optional[InsightOutput]:
        """
        Phase 1 cache check.
        Returns a COMPLETE InsightOutput for the same tenant + template + entities
        that was completed within the last staleness_days, or None.
        """
