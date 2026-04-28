"""
Connections router — service-integration OAuth flows + management.

Distinct from `/api/v1/auth/...` which handles SIGN-IN. This router
handles the user CONNECTING third-party services (Calendar, Mail,
OneDrive, etc.) AFTER they're already signed in.

Endpoints:

  GET  /api/v1/connections/services
        List every service we can integrate (data-driven from
        oauth_scopes.SERVICES). Response includes whether each provider
        is configured (so frontend hides services that can't work yet).

  GET  /api/v1/connections
        List the current user's active connections — service name,
        external account label, last sync time, sync_enabled flag.

  GET  /api/v1/connections/connect/{service_id}
        Start the OAuth consent flow for a service. Redirects to the
        IdP. After consent, IdP redirects back to /callback.

  GET  /api/v1/connections/callback/{service_id}
        IdP redirects here after consent. Exchanges the code for a
        token, upserts a UserCredential row, redirects to FRONTEND_URL
        with a success or error query param.

  POST /api/v1/connections/{credential_id}/disconnect
        Revoke a credential — wipes encrypted tokens, sets revoked_at,
        disables sync.

  POST /api/v1/connections/{credential_id}/toggle-sync
        Pause / resume background sync without disconnecting.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import require_user
from backend.app.core.config import settings
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.credential_orm import UserCredential
from backend.app.models.orm.user_orm import AppUser
from backend.app.services.auth.credential_service import (
    list_credentials_for_user, revoke_credential, upsert_credential,
)
from backend.app.services.auth.oauth_providers import is_configured
from backend.app.services.auth.oauth_scopes import SERVICES, get_service


router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers — per-service Authlib client
# ---------------------------------------------------------------------------
#
# We can't reuse `auth.oauth_providers.get_provider("google")` directly
# because that client requests only the sign-in scopes. For service
# connections we need different per-service scopes, so each service gets
# its own Authlib client instance with the right scope set.

_service_oauth_singletons: dict[str, OAuth] = {}


def _service_client(service_id: str):
    """Return an Authlib OAuth client for `service_id`. Lazy-built and
    cached. None if the underlying provider isn't configured."""
    spec = get_service(service_id)
    if not is_configured(spec["provider"]):
        return None
    if service_id in _service_oauth_singletons:
        oauth = _service_oauth_singletons[service_id]
    else:
        oauth = OAuth()
        if spec["provider"] == "google":
            oauth.register(
                name=service_id,  # use service_id as registered name to keep them distinct
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                server_metadata_url=(
                    "https://accounts.google.com/.well-known/openid-configuration"
                ),
                # client_kwargs only carries `scope` reliably across Authlib
                # versions. access_type / prompt go to authorize_redirect()
                # below as authorize-request params (see `connect`).
                client_kwargs={"scope": " ".join(spec["scopes"])},
            )
        elif spec["provider"] == "microsoft":
            # Microsoft `common` tenant gotcha: the OIDC discovery doc
            # advertises `iss` as a literal placeholder
            # (https://login.microsoftonline.com/{tenantid}/v2.0), but
            # actual ID tokens carry the user's real tenant GUID. Authlib's
            # strict OIDC validator rejects the mismatch with
            # InvalidClaimError: 'iss'.
            #
            # Workaround: don't use server_metadata_url. Provide explicit
            # endpoints. With no OIDC discovery, Authlib won't try to parse
            # / validate the ID token — we just take the access_token and
            # call Graph `/me` for the user info (which the callback below
            # already does as the userinfo_endpoint).
            tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
            oauth.register(
                name=service_id,
                client_id=settings.MICROSOFT_OAUTH_CLIENT_ID,
                client_secret=settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                authorize_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
                access_token_url=f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                userinfo_endpoint="https://graph.microsoft.com/v1.0/me",
                client_kwargs={"scope": " ".join(spec["scopes"])},
            )
        _service_oauth_singletons[service_id] = oauth
    return oauth.create_client(service_id)


def _connect_callback_url(service_id: str) -> str:
    base = settings.OAUTH_REDIRECT_BASE.rstrip("/")
    return f"{base}{settings.API_V1_STR}/connections/callback/{service_id}"


# ---------------------------------------------------------------------------
# List services + active connections
# ---------------------------------------------------------------------------

@router.get("/services")
def list_services():
    """Catalogue of services we can integrate. Frontend uses this to
    render the "Connected accounts" page."""
    out = []
    for service_id, spec in SERVICES.items():
        out.append({
            "service_id":   service_id,
            "provider":     spec["provider"],
            "display_name": spec["display_name"],
            "scopes":       spec["scopes"],
            "available":    is_configured(spec["provider"]),
        })
    return {"services": out}


@router.get("")
def list_my_connections(
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    creds = list_credentials_for_user(db, user.id)
    return {
        "connections": [
            {
                "id":                  str(c.id),
                "service_id":          c.service,
                "provider":            c.provider,
                "external_account":    c.external_account_label or c.external_account_id,
                "scopes":              c.scopes or [],
                "sync_enabled":        c.sync_enabled,
                "last_synced_at":      c.last_synced_at.isoformat() if c.last_synced_at else None,
                "last_sync_error":     c.last_sync_error,
                "created_at":          c.created_at.isoformat() if c.created_at else None,
            }
            for c in creds
        ],
    }


# ---------------------------------------------------------------------------
# OAuth connect flow
# ---------------------------------------------------------------------------

@router.get("/connect/{service_id}")
async def connect(
    request: Request,
    service_id: str = Path(...),
    user: AppUser = Depends(require_user),
):
    try:
        spec = get_service(service_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown service '{service_id}'")
    if not is_configured(spec["provider"]):
        raise HTTPException(
            status_code=503,
            detail=f"{spec['provider']} OAuth not configured (set "
                   f"{spec['provider'].upper()}_OAUTH_CLIENT_ID + ..._SECRET in .env)",
        )
    client = _service_client(service_id)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth client unavailable")
    redirect_uri = _connect_callback_url(service_id)
    # Stash the user_id in the OAuth state so the callback knows which
    # AppUser to attach the credential to. Authlib uses Starlette session
    # for the OAuth state; we add an extra request.session field for
    # the user.
    request.session["connect_user_id"] = str(user.id)

    # Provider-specific authorize-request params:
    #   Google: access_type=offline + prompt=consent forces Google to
    #     return a refresh_token (otherwise it omits one if the user has
    #     a prior grant for the same OAuth client). include_granted_scopes
    #     defaults to false (we want the new grant explicit, not bundled).
    #   Microsoft: prompt=consent forces a fresh consent dialog so the
    #     user can sign in with a different account if they want.
    auth_extra: dict[str, str] = {}
    if spec["provider"] == "google":
        auth_extra["access_type"] = "offline"
        auth_extra["prompt"] = "consent"
    elif spec["provider"] == "microsoft":
        auth_extra["prompt"] = "consent"

    return await client.authorize_redirect(request, redirect_uri, **auth_extra)


@router.get("/callback/{service_id}")
async def callback(
    request: Request,
    service_id: str = Path(...),
    db: Session = Depends(get_phase2_session),
):
    try:
        spec = get_service(service_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown service '{service_id}'")
    if not is_configured(spec["provider"]):
        raise HTTPException(status_code=503, detail="OAuth not configured")

    user_id = request.session.pop("connect_user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing connect state — did you start from /connect?",
        )

    client = _service_client(service_id)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as e:
        # Redirect back to frontend with an error toast.
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/settings/connections?error={e.error}",
            status_code=status.HTTP_302_FOUND,
        )

    # Pull the IdP-provided user info to label the credential by
    # external account email/UPN. We accept fields from both OIDC ID
    # tokens (sub, email, name) AND Microsoft Graph /me (id,
    # userPrincipalName, mail, displayName).
    userinfo = token.get("userinfo")
    if userinfo is None:
        try:
            userinfo = await client.userinfo(token=token)
        except Exception:
            userinfo = {}
    external_id = (
        userinfo.get("sub")              # OIDC standard
        or userinfo.get("oid")           # Microsoft ID token (object ID)
        or userinfo.get("id")            # Graph /me (object ID)
        or userinfo.get("email")
        or userinfo.get("mail")          # Graph /me primary mail
        or userinfo.get("userPrincipalName")
        or "unknown"
    )
    external_label = (
        userinfo.get("email")
        or userinfo.get("mail")          # Graph /me
        or userinfo.get("preferred_username")
        or userinfo.get("upn")
        or userinfo.get("userPrincipalName")
        or userinfo.get("displayName")   # Graph /me display name
        or external_id
    )

    upsert_credential(
        db,
        user_id=UUID(user_id),
        service=service_id,
        provider=spec["provider"],
        external_account_id=str(external_id),
        external_account_label=str(external_label),
        token_response=token,
    )
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/settings/connections?connected={service_id}",
        status_code=status.HTTP_302_FOUND,
    )


# ---------------------------------------------------------------------------
# Disconnect / pause sync
# ---------------------------------------------------------------------------

@router.post("/{credential_id}/disconnect")
def disconnect(
    credential_id: UUID,
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    cred = (
        db.query(UserCredential)
        .filter(
            UserCredential.id == credential_id,
            UserCredential.user_id == user.id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=404, detail="connection not found")
    revoke_credential(db, cred)
    return {"ok": True}


@router.post("/{credential_id}/toggle-sync")
def toggle_sync(
    credential_id: UUID,
    db: Session = Depends(get_phase2_session),
    user: AppUser = Depends(require_user),
):
    cred = (
        db.query(UserCredential)
        .filter(
            UserCredential.id == credential_id,
            UserCredential.user_id == user.id,
            UserCredential.revoked_at.is_(None),
        )
        .first()
    )
    if cred is None:
        raise HTTPException(status_code=404, detail="connection not found")
    cred.sync_enabled = not cred.sync_enabled
    db.commit()
    return {"ok": True, "sync_enabled": cred.sync_enabled}
