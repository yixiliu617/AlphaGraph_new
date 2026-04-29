"""
/api/v1/me/notes/* — the signed-in user's synced notes (OneNote, etc.).

Read-only. Writes happen via the OAuth connect flow + scheduled sync.
For now we serve OneNote pages; future: Google Keep, Apple Notes, ...

Endpoints:

  GET  /api/v1/me/notes/list
        Recent notes — title + notebook + section + last_modified_at,
        without the heavy `content_html`. Default 50 most recently
        modified, optional `?notebook=...` filter.

  GET  /api/v1/me/notes/{id}
        One note's full content (HTML + plaintext).

  GET  /api/v1/me/notes/search?q=...
        Plain-text substring search across `content_text`. Will gain a
        proper tsvector index later — fine for now at our scale.

  POST /api/v1/me/notes/sync
        Manual sync trigger.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import require_user
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.note_synced_orm import UserNote
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.models.orm.user_orm import AppUser
from backend.app.services.integrations.sync_runner import sync_credential


router = APIRouter()


def _summary(n: UserNote) -> dict:
    """Trimmed serialisation for list views — no heavy content fields."""
    return {
        "id":            str(n.id),
        "provider":      n.provider,
        "title":         n.title,
        "notebook":      n.notebook_name,
        "section":       n.section_name,
        "page_link":     n.page_link,
        "last_modified": n.last_modified_at_remote.isoformat() if n.last_modified_at_remote else None,
        "preview":       (n.content_text or "")[:200] or None,
        "truncated":     n.content_truncated,
    }


@router.get("/list")
def list_notes(
    notebook: Optional[str] = Query(None, description="Exact notebook name match"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    q = (
        db.query(UserNote)
        .filter(UserNote.user_id == user.id)
        .order_by(UserNote.last_modified_at_remote.desc().nullslast())
    )
    if notebook:
        q = q.filter(UserNote.notebook_name == notebook)
    rows = q.limit(limit).all()

    # Notebook list for the filter dropdown
    notebooks = (
        db.query(UserNote.notebook_name)
        .filter(UserNote.user_id == user.id, UserNote.notebook_name.isnot(None))
        .distinct()
        .order_by(UserNote.notebook_name.asc())
        .all()
    )

    return {
        "notes":     [_summary(n) for n in rows],
        "notebooks": [nb[0] for nb in notebooks],
    }


@router.get("/search")
def search_notes(
    q: str = Query(..., min_length=2, description="Plain-text substring"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    needle = f"%{q}%"
    rows = (
        db.query(UserNote)
        .filter(
            UserNote.user_id == user.id,
            or_(
                UserNote.title.ilike(needle),
                UserNote.content_text.ilike(needle),
                UserNote.notebook_name.ilike(needle),
            ),
        )
        .order_by(UserNote.last_modified_at_remote.desc().nullslast())
        .limit(limit)
        .all()
    )
    return {"notes": [_summary(n) for n in rows]}


@router.get("/{note_id}")
def get_note(
    note_id: UUID,
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    n = (
        db.query(UserNote)
        .filter(UserNote.id == note_id, UserNote.user_id == user.id)
        .first()
    )
    if n is None:
        raise HTTPException(status_code=404, detail="note not found")
    return {
        **_summary(n),
        "content_html": n.content_html,
        "content_text": n.content_text,
    }


@router.post("/sync")
def manual_sync(
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    creds = (
        db.query(UserCredential)
        .filter(
            UserCredential.user_id == user.id,
            UserCredential.revoked_at.is_(None),
            UserCredential.sync_enabled.is_(True),
            UserCredential.service == "microsoft.onenote",
        )
        .all()
    )
    out = []
    for cred in creds:
        r = sync_credential(db, cred)
        out.append({
            "service":  cred.service,
            "ok":       r.ok,
            "inserted": r.inserted,
            "updated":  r.updated,
            "error":    r.error,
        })
    return {"results": out}
