"""
/api/v1/me/calendar/* — the signed-in user's synced calendar events.

Read-only endpoints; writes happen via the connect flow + the sync
runner. No POST /events here — we don't write events back to the user's
Google / Outlook calendar (would need writeable scopes which we don't
request).

Endpoints:

  GET /api/v1/me/calendar/events?days=7
        Upcoming events in the next `days` days, merged across all
        the user's connected calendar credentials. Sorted by start_at.

  POST /api/v1/me/calendar/sync
        Manual sync trigger — runs sync_runner against this user's
        active calendar credentials right now (force_all=True).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import require_user
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.calendar_event_orm import UserCalendarEvent
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.models.orm.user_orm import AppUser
from backend.app.services.integrations.sync_runner import sync_credential


router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/events")
def list_events(
    days: int = Query(7, ge=1, le=180),
    include_past_today: bool = Query(False, description="If true, also include events earlier today"),
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    """Upcoming calendar events for the signed-in user."""
    if include_past_today:
        cutoff = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        cutoff = _now()
    end = _now() + timedelta(days=days)

    rows = (
        db.query(UserCalendarEvent)
        .filter(
            UserCalendarEvent.user_id == user.id,
            UserCalendarEvent.status == "confirmed",
            UserCalendarEvent.start_at >= cutoff,
            UserCalendarEvent.start_at <= end,
        )
        .order_by(UserCalendarEvent.start_at.asc())
        .all()
    )
    return {
        "events": [
            {
                "id":         str(r.id),
                "provider":   r.provider,
                "title":      r.title,
                "location":   r.location,
                "html_link":  r.html_link,
                "start_at":   r.start_at.isoformat() if r.start_at else None,
                "end_at":     r.end_at.isoformat() if r.end_at else None,
                "all_day":    r.all_day,
                "attendees":  r.attendees or [],
                "organizer":  r.organizer,
                "description": (r.description or "")[:500] or None,
            }
            for r in rows
        ],
    }


@router.post("/sync")
def manual_sync(
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    """Run sync immediately for this user's active calendar credentials."""
    creds = (
        db.query(UserCredential)
        .filter(
            UserCredential.user_id == user.id,
            UserCredential.revoked_at.is_(None),
            UserCredential.sync_enabled.is_(True),
            UserCredential.service.in_(["google.calendar", "microsoft.calendar"]),
        )
        .all()
    )
    out = []
    for cred in creds:
        r = sync_credential(db, cred)
        out.append({
            "service":   cred.service,
            "ok":        r.ok,
            "inserted":  r.inserted,
            "updated":   r.updated,
            "error":     r.error,
        })
    return {"results": out}
