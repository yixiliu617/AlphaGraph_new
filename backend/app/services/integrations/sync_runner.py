"""
Shared sync runner — calls the right adapter for a credential, handles
token refresh + error capture + cursor persistence.

Two entry points:

  - sync_credential(db, cred): run sync once for one credential row.
    Used by the CLI ad-hoc invocation + the scheduler's per-tick worker.

  - sync_all_due(): the scheduler's tick — pick every credential whose
    last_synced_at is older than its service's `sync_minutes`, sync each.

The runner is data-driven: each adapter advertises its `service_id` and
the `_ADAPTERS` registry maps service_id → adapter class. Adding a new
service is one entry here + one adapter file.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.auth.credential_service import (
    get_decrypted_tokens, is_access_token_expired, refresh_access_token,
    update_sync_state,
)
from backend.app.services.auth.oauth_scopes import SERVICES, get_service
from backend.app.services.integrations.base import BaseIntegrationAdapter, SyncResult
from backend.app.services.integrations.google.calendar import GoogleCalendarAdapter
from backend.app.services.integrations.microsoft.calendar import OutlookCalendarAdapter
from backend.app.services.integrations.microsoft.onenote import OneNoteAdapter


logger = logging.getLogger(__name__)


_ADAPTERS: dict[str, type[BaseIntegrationAdapter]] = {
    "google.calendar":    GoogleCalendarAdapter,
    "microsoft.calendar": OutlookCalendarAdapter,
    "microsoft.onenote":  OneNoteAdapter,
    # Future:
    # "microsoft.outlook_mail": OutlookMailAdapter,
    # "google.docs":            GoogleDocsAdapter,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sync_credential(db: Session, cred: UserCredential) -> SyncResult:
    """Run one sync cycle for a single credential. Auto-refreshes the
    access token if expired. Persists cursor + sync state.
    """
    if cred.revoked_at is not None:
        return SyncResult(error=f"credential {cred.id} is revoked")

    adapter_cls = _ADAPTERS.get(cred.service)
    if adapter_cls is None:
        return SyncResult(error=f"no adapter for service {cred.service!r}")

    # Refresh access token if expired (or about to be).
    if is_access_token_expired(cred):
        try:
            access_token = refresh_access_token(db, cred)
            logger.info("refreshed access token for %s", cred.service)
        except Exception as e:  # noqa: BLE001
            err = f"refresh_access_token failed: {e}"
            update_sync_state(db, cred, success=False, error=err)
            return SyncResult(error=err)
    else:
        access_token = get_decrypted_tokens(cred)["access_token"]

    adapter = adapter_cls()
    try:
        result = adapter.sync(db, cred, access_token)
    except Exception as e:  # noqa: BLE001
        err = f"adapter.sync raised {type(e).__name__}: {e}"
        logger.exception("sync raised: %s", err)
        update_sync_state(db, cred, success=False, error=err)
        return SyncResult(error=err)

    if result.error:
        update_sync_state(db, cred, success=False, error=result.error)
    else:
        # Commit the upserts, then record success on the credential.
        db.commit()
        update_sync_state(db, cred, success=True, cursor=result.new_cursor)

    return result


def sync_all_due(*, force_all: bool = False) -> list[dict]:
    """Iterate every active credential and sync if due.

    `force_all=True` ignores the per-service cadence and syncs everything;
    useful for the ad-hoc CLI run.
    """
    out: list[dict] = []
    db = Phase2SessionLocal()
    try:
        creds = (
            db.query(UserCredential)
            .filter(
                UserCredential.revoked_at.is_(None),
                UserCredential.sync_enabled.is_(True),
                UserCredential.service.in_(_ADAPTERS.keys()),
            )
            .all()
        )
        now = _now()
        for cred in creds:
            spec = SERVICES.get(cred.service)
            cadence = (spec or {}).get("sync_minutes", 30)
            due_at = (
                cred.last_synced_at + timedelta(minutes=cadence)
                if cred.last_synced_at else None
            )
            if not force_all and due_at and due_at > now:
                out.append({
                    "service":   cred.service,
                    "user_id":   str(cred.user_id),
                    "skipped":   True,
                    "reason":    f"not due until {due_at.isoformat()}",
                })
                continue

            r = sync_credential(db, cred)
            out.append({
                "service":     cred.service,
                "user_id":     str(cred.user_id),
                "ok":          r.ok,
                "inserted":    r.inserted,
                "updated":     r.updated,
                "skipped":     r.skipped,
                "full_resync": r.full_resync_required,
                "error":       r.error,
            })
    finally:
        db.close()
    return out
