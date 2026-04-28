"""
OAuth provider configuration.

Two providers (per locked decision D10): Google and Microsoft. Both go
direct — no Auth0, no third-party identity broker. We use Authlib's
async OAuth client which handles the OIDC discovery + JWKS validation
for us.

Per-provider configuration:

  Google
    client_id     / client_secret  : Google Cloud Console -> APIs & Services
                                     -> Credentials -> OAuth 2.0 Client IDs
    redirect_uri  : <OAUTH_REDIRECT_BASE>/api/v1/auth/google/callback
    scopes        : openid email profile
    discovery URL : https://accounts.google.com/.well-known/openid-configuration

  Microsoft (Entra / Azure AD)
    client_id / secret : Azure Portal -> App Registrations -> New registration
                         -> Authentication -> Add platform: Web -> Redirect URI
    redirect_uri       : <OAUTH_REDIRECT_BASE>/api/v1/auth/microsoft/callback
    scopes             : openid email profile offline_access
    tenant             : "common" supports both personal MSA and work/school
                         accounts. For an org-only deployment use the tenant id.
    discovery URL      : https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration

The OAuth instance below is module-level (constructed lazily on first
use) so importing this module is cheap. If a provider is not configured
(no client_id), `get_provider()` returns None and the auth router
returns a 503 for that provider's login endpoint with a hint to set
the env vars.
"""
from __future__ import annotations

from typing import Optional

from authlib.integrations.starlette_client import OAuth

from backend.app.core.config import settings


_oauth_singleton: Optional[OAuth] = None


def _build_oauth() -> OAuth:
    o = OAuth()
    if settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET:
        o.register(
            name="google",
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if settings.MICROSOFT_OAUTH_CLIENT_ID and settings.MICROSOFT_OAUTH_CLIENT_SECRET:
        tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
        o.register(
            name="microsoft",
            client_id=settings.MICROSOFT_OAUTH_CLIENT_ID,
            client_secret=settings.MICROSOFT_OAUTH_CLIENT_SECRET,
            server_metadata_url=(
                f"https://login.microsoftonline.com/{tenant}"
                "/v2.0/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile offline_access"},
        )
    return o


def get_oauth() -> OAuth:
    global _oauth_singleton
    if _oauth_singleton is None:
        _oauth_singleton = _build_oauth()
    return _oauth_singleton


def get_provider(name: str):
    """Return the Authlib OAuth client for `name`, or None if not configured."""
    if name not in {"google", "microsoft"}:
        return None
    o = get_oauth()
    try:
        return o.create_client(name)
    except Exception:
        return None


def is_configured(name: str) -> bool:
    """Cheap check used by routes to decide whether to advertise a provider."""
    if name == "google":
        return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)
    if name == "microsoft":
        return bool(settings.MICROSOFT_OAUTH_CLIENT_ID and settings.MICROSOFT_OAUTH_CLIENT_SECRET)
    return False


def callback_url(provider: str) -> str:
    """Absolute URL the IdP redirects back to after consent."""
    base = settings.OAUTH_REDIRECT_BASE.rstrip("/")
    return f"{base}{settings.API_V1_STR}/auth/{provider}/callback"
