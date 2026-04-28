"""
Auth router — Google + Microsoft OAuth flows + session cookie endpoints.

Endpoints:

  GET  /api/v1/auth/{provider}/login
        Redirects the browser to the IdP consent screen. provider in
        {"google", "microsoft"}. 503 if that provider isn't configured.

  GET  /api/v1/auth/{provider}/callback
        IdP redirects here after consent. Exchanges the code for an
        id_token, upserts the AppUser, creates an OAuthSession, sets the
        session cookie, redirects to FRONTEND_URL.

  POST /api/v1/auth/logout
        Revokes the active session and clears the cookie.

  GET  /api/v1/auth/me
        Returns the current user (or 401). Cheap read for the frontend
        to decide whether to show "Sign in" or "<email> v"  in the nav.

  GET  /api/v1/auth/providers
        Lists which providers are configured. Frontend uses this to
        decide which login buttons to show.
"""
from __future__ import annotations

from typing import Optional

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import get_current_user, require_user
from backend.app.core.config import settings
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.user_orm import AppUser, OAuthSession
from backend.app.services.auth.jwt_token import issue_access_token
from backend.app.services.auth.oauth_providers import (
    callback_url, get_provider, is_configured,
)
from backend.app.services.auth.session_service import (
    attach_access_token_jti, create_session, find_active_session,
    revoke_session, upsert_user_from_idtoken,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Provider discovery (frontend uses this to decide which buttons to show)
# ---------------------------------------------------------------------------

@router.get("/providers")
def list_providers():
    return {
        "google":    is_configured("google"),
        "microsoft": is_configured("microsoft"),
    }


# ---------------------------------------------------------------------------
# Login / callback
# ---------------------------------------------------------------------------

@router.get("/{provider}/login")
async def login(
    request: Request,
    provider: str = Path(..., pattern="^(google|microsoft)$"),
):
    if not is_configured(provider):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider} OAuth not configured (set "
                   f"{provider.upper()}_OAUTH_CLIENT_ID + ..._SECRET in .env)",
        )
    client = get_provider(provider)
    redirect_uri = callback_url(provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/{provider}/callback")
async def callback(
    request: Request,
    response: Response,
    provider: str = Path(..., pattern="^(google|microsoft)$"),
    db: Session = Depends(get_phase2_session),
):
    if not is_configured(provider):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider} OAuth not configured",
        )
    client = get_provider(provider)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth callback failed: {e.error}",
        )

    # The id_token is parsed by Authlib via OIDC discovery — we just read claims.
    userinfo = token.get("userinfo")
    if userinfo is None:
        # Microsoft sometimes returns claims via a separate userinfo call.
        userinfo = await client.userinfo(token=token)

    subject_id = userinfo.get("sub")
    email      = userinfo.get("email") or userinfo.get("preferred_username")
    name       = userinfo.get("name")

    if not subject_id or not email:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OAuth response missing required claims (sub/email)",
        )

    user = upsert_user_from_idtoken(
        db, provider=provider, subject_id=subject_id,
        email=email, name=name,
    )

    sess, _refresh_token_raw = create_session(
        db, user=user,
        ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )

    access_token, jti, expires_at = issue_access_token(
        user_id=user.id, session_id=sess.id,
        email=user.email, tier=user.tier,
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    attach_access_token_jti(db, session=sess, jti=jti)

    redirect = RedirectResponse(
        url=settings.FRONTEND_URL,
        status_code=status.HTTP_302_FOUND,
    )
    redirect.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        domain=settings.SESSION_COOKIE_DOMAIN,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return redirect


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_phase2_session),
    user: Optional[AppUser] = Depends(get_current_user),
):
    """Revoke the active session and clear the cookie. Idempotent —
    already-logged-out callers also get a 200."""
    if user is not None:
        # Find the matching session via the cookie's JWT sid claim.
        from backend.app.services.auth.jwt_token import verify_access_token
        token = request.cookies.get(settings.SESSION_COOKIE_NAME)
        payload = verify_access_token(token) if token else None
        sid = payload.get("sid") if payload else None
        if sid:
            sess = find_active_session(db, session_id=sid, user_id=user.id)
            if sess is not None:
                revoke_session(db, session=sess)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        domain=settings.SESSION_COOKIE_DOMAIN,
        path="/",
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

@router.get("/me")
def me(user: Optional[AppUser] = Depends(get_current_user)):
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    return {
        "id":             str(user.id),
        "email":          user.email,
        "name":           user.name,
        "tier":           user.tier,
        "oauth_provider": user.oauth_provider,
        "is_active":      user.is_active,
        "created_at":     user.created_at.isoformat() if user.created_at else None,
        "last_seen_at":   user.last_seen_at.isoformat() if user.last_seen_at else None,
    }
