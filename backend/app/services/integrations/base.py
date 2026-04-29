"""
Adapter interface for service integrations.

Each integration (Google Calendar, Outlook Mail, OneNote, ...) implements
a small adapter class. The shared `sync_runner` calls these via a
uniform protocol — auto-refreshes expired tokens, runs the adapter's
`sync()` method, persists whatever rows the adapter returns, updates
sync state on the credential.

Why a class (not just a function): we want one stateful place to
encapsulate per-provider quirks — Google's syncToken vs Microsoft's
delta link, Google's 410 "sync token expired" recovery vs Microsoft's
cursor-rotation, attendee shape differences, etc.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.app.models.orm.credential_orm import UserCredential


@dataclass
class SyncResult:
    """Outcome of one sync run for one credential."""
    inserted: int = 0
    updated:  int = 0
    deleted:  int = 0
    skipped:  int = 0
    new_cursor: Optional[str] = None
    full_resync_required: bool = False
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class BaseIntegrationAdapter(ABC):
    """One adapter per (provider, service) pair — e.g. GoogleCalendarAdapter,
    OutlookCalendarAdapter, OneNoteAdapter.
    """

    #: Service id (e.g. "google.calendar"); matches oauth_scopes.SERVICES key.
    service_id: str = ""

    @abstractmethod
    def sync(
        self,
        db: Session,
        cred: UserCredential,
        access_token: str,
    ) -> SyncResult:
        """Pull new / updated rows from the upstream API and upsert into
        whatever Postgres table this integration owns.

        Args:
          db:           live SQLAlchemy session bound to phase2_engine
          cred:         the UserCredential row (caller has refreshed the
                        access token if needed; call `access_token` for
                        the fresh string)
          access_token: short-lived bearer token to send to the upstream
                        API. Caller has already refreshed if expired.

        Returns:
          SyncResult with the counts + new cursor (if any).

        Implementation guidance:
          - Read `cred.last_sync_cursor` for incremental sync. If the
            upstream rejects the cursor (typical: stale > N days), set
            full_resync_required=True and refetch from scratch.
          - Set `last_sync_cursor` on the SyncResult so the runner can
            persist it after success.
          - Don't catch HTTP errors silently — let them bubble so the
            runner records them on the credential.
          - Don't commit the session; the runner does that after
            attaching the SyncResult.
        """
        raise NotImplementedError
