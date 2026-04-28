"""
DB helpers for `user_credential` — upsert from an OAuth token response,
fetch decrypted credentials for API calls, refresh expired access tokens.

The encryption boundary lives here. Callers see plaintext strings only
inside this module; `UserCredential.{access,refresh}_token_encrypted`
columns hold raw Fernet bytes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.models.orm.credential_orm import UserCredential
from backend.app.services.auth.encryption import (
    TokenEncryptionError, decrypt_str, encrypt_str,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at_from_token(token: dict) -> Optional[datetime]:
    """`token` is the dict Authlib returns from authorize_access_token().
    Honors `expires_at` (epoch seconds) if present, falling back to
    `expires_in` (seconds-from-now)."""
    if "expires_at" in token and token["expires_at"]:
        try:
            return datetime.fromtimestamp(int(token["expires_at"]), tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    if "expires_in" in token and token["expires_in"]:
        try:
            return _now() + timedelta(seconds=int(token["expires_in"]))
        except (TypeError, ValueError):
            pass
    return None


def upsert_credential(
    db: Session,
    *,
    user_id: UUID,
    service: str,
    provider: str,
    external_account_id: str,
    external_account_label: Optional[str],
    token_response: dict,
) -> UserCredential:
    """Insert or update a credential row from an Authlib token response.

    Idempotent on (user_id, service, external_account_id). Re-running
    after a re-consent updates the access/refresh tokens + scopes
    in place.
    """
    access_token = token_response.get("access_token")
    refresh_token = token_response.get("refresh_token")
    expires_at = _expires_at_from_token(token_response)
    granted_scope_str = token_response.get("scope") or ""
    granted_scopes = (
        [s for s in granted_scope_str.split() if s]
        if isinstance(granted_scope_str, str) else []
    )

    cred = (
        db.query(UserCredential)
        .filter(
            UserCredential.user_id == user_id,
            UserCredential.service == service,
            UserCredential.external_account_id == external_account_id,
        )
        .first()
    )
    if cred is None:
        cred = UserCredential(
            user_id=user_id,
            service=service,
            provider=provider,
            external_account_id=external_account_id,
            external_account_label=external_account_label,
            scopes=granted_scopes,
        )
        db.add(cred)

    # Update token + metadata. Refresh tokens may not be present on
    # subsequent consents (Google only returns it on the first one
    # unless `prompt=consent` is set) — keep the existing one if so.
    cred.external_account_label = external_account_label
    cred.access_token_encrypted = encrypt_str(access_token) if access_token else cred.access_token_encrypted
    if refresh_token:
        cred.refresh_token_encrypted = encrypt_str(refresh_token)
    cred.access_token_expires_at = expires_at
    if granted_scopes:
        cred.scopes = granted_scopes
    cred.revoked_at = None  # connect re-activates a previously revoked row

    db.commit()
    db.refresh(cred)
    return cred


def get_decrypted_tokens(cred: UserCredential) -> dict[str, Optional[str]]:
    """Decrypt the access and refresh tokens. Returns:
        {"access_token": str|None, "refresh_token": str|None,
         "expires_at":   datetime|None}
    Raises TokenEncryptionError if either ciphertext is malformed."""
    return {
        "access_token":  decrypt_str(cred.access_token_encrypted),
        "refresh_token": decrypt_str(cred.refresh_token_encrypted),
        "expires_at":    cred.access_token_expires_at,
    }


def is_access_token_expired(
    cred: UserCredential, *, leeway_seconds: int = 60,
) -> bool:
    """True if the stored access token is expired or about to expire.
    `leeway_seconds` (default 60) avoids races where we check, then
    immediately make a request, and the token expires in flight."""
    if cred.access_token_expires_at is None:
        return True
    return cred.access_token_expires_at <= _now() + timedelta(seconds=leeway_seconds)


def refresh_access_token(db: Session, cred: UserCredential) -> str:
    """Exchange the stored refresh_token for a new access token. Updates
    the cred row in place and returns the fresh access token string.

    Raises:
      ValueError if there's no refresh token on the row.
      requests.HTTPError if the IdP refuses (e.g. revoked grant) — caller
        should mark the credential as revoked and prompt the user to
        reconnect.
    """
    import requests

    from backend.app.core.config import settings

    refresh_token = decrypt_str(cred.refresh_token_encrypted)
    if not refresh_token:
        raise ValueError(
            f"credential {cred.id} has no refresh_token; user must reconnect"
        )

    if cred.provider == "google":
        token_url = "https://oauth2.googleapis.com/token"
        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
    elif cred.provider == "microsoft":
        tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        client_id = settings.MICROSOFT_OAUTH_CLIENT_ID
        client_secret = settings.MICROSOFT_OAUTH_CLIENT_SECRET
    else:
        raise ValueError(f"unknown provider {cred.provider!r}")

    resp = requests.post(
        token_url,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    new_access = payload.get("access_token")
    if not new_access:
        raise ValueError(f"token response missing access_token: {payload!r}")

    # Some IdPs (Google) rotate the refresh token on refresh; some don't.
    # Update if present, leave existing in place otherwise.
    new_refresh = payload.get("refresh_token")
    cred.access_token_encrypted = encrypt_str(new_access)
    if new_refresh:
        cred.refresh_token_encrypted = encrypt_str(new_refresh)
    cred.access_token_expires_at = _expires_at_from_token(payload)
    db.commit()
    db.refresh(cred)
    return new_access


def list_credentials_for_user(
    db: Session, user_id: UUID, *, include_revoked: bool = False,
) -> list[UserCredential]:
    q = db.query(UserCredential).filter(UserCredential.user_id == user_id)
    if not include_revoked:
        q = q.filter(UserCredential.revoked_at.is_(None))
    return q.order_by(UserCredential.service, UserCredential.created_at).all()


def revoke_credential(db: Session, cred: UserCredential) -> None:
    cred.revoked_at = _now()
    cred.sync_enabled = False
    # Wipe the tokens — revoked credentials shouldn't be useful even if
    # the encryption key leaks later.
    cred.access_token_encrypted = None
    cred.refresh_token_encrypted = None
    cred.access_token_expires_at = None
    db.commit()


def update_sync_state(
    db: Session, cred: UserCredential, *,
    cursor: Optional[str] = None,
    error:  Optional[str] = None,
    success: bool = True,
) -> None:
    """Called by sync workers after a sync attempt."""
    if success:
        cred.last_synced_at = _now()
        cred.last_sync_error = None
        if cursor is not None:
            cred.last_sync_cursor = cursor
    else:
        cred.last_sync_error = (error or "")[:4000]
    db.commit()
