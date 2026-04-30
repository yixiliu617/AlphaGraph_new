"""
FastAPI dependencies for auth-protected endpoints.

Lives in `auth_deps.py` (not `dependencies.py`) because the existing
`backend.app.api.dependencies` module already exports
`get_extraction_runner` and other unrelated helpers. Keeping auth in a
separate module avoids loading the rest of the dependency wiring
(extraction runners, etc.) when auth is the only thing a router needs.

Usage in a router:

    from fastapi import Depends
    from backend.app.api.auth_deps import get_current_user, require_user

    @router.get("/me")
    def me(user = Depends(get_current_user)):
        return user            # AppUser or None — endpoint handles guest case

    @router.get("/dashboard")
    def dashboard(user = Depends(require_user)):
        return {"user_id": user.id}    # 401 if no valid session
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.user_orm import AppUser
from backend.app.services.auth.jwt_token import verify_access_token
from backend.app.services.auth.session_service import find_active_session


def get_current_user(
    request: Request,
    db: Session = Depends(get_phase2_session),
) -> Optional[AppUser]:
    """Return the AppUser tied to the session cookie, or None if no
    valid session. Does NOT raise on missing/invalid — endpoints that
    need 401 should depend on `require_user` instead.

    The session cookie name is `settings.SESSION_COOKIE_NAME` (configurable).
    FastAPI's `Cookie(alias=...)` can't take a dynamic name, so we read it
    off the request directly.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        return None
    payload = verify_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    sid = payload.get("sid")
    if not user_id or not sid:
        return None
    # Validate the session is still active (revoke-on-logout).
    sess = find_active_session(db, session_id=sid, user_id=user_id)
    if sess is None:
        return None
    user = db.get(AppUser, user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_user(
    user: Optional[AppUser] = Depends(get_current_user),
) -> AppUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(current_user=Depends(require_user)):
    """403 unless the user has admin_role='admin'."""
    if getattr(current_user, "admin_role", "user") != "admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return current_user
