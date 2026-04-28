"""
JWT issue + verify for the session cookie.

The cookie carries a short-lived (1 hour by default) JWT containing the
user_id and the oauth_session.id. The refresh path (TODO in B2 follow-
up) compares the JWT's session_id claim against `oauth_session` rows
that have a non-revoked, non-expired refresh-token-hash on file.

Design choice — JWT vs. opaque session token:
  - JWT: cheap to verify (no DB hit on the read path), self-describing.
  - Opaque: revocation is one-row update, but every request hits the DB.

We use JWT for the access token (cheap reads) + opaque refresh tokens
in the DB (revocable). Logout = delete the oauth_session row, so the
refresh path stops working; the JWT cookie keeps working until it
expires (max 1h by default), which is acceptable.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from jwt import PyJWTError

from backend.app.core.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_access_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    email: str,
    tier: str,
    minutes: int = 60,
) -> tuple[str, str, datetime]:
    """Mint a JWT access token. Returns (token, jti, expires_at).

    The `jti` (JWT ID) is stored on the oauth_session row so a future
    revocation can match the session by the access token's jti claim.
    """
    jti = secrets.token_hex(16)  # 32-char hex
    expires_at = _now() + timedelta(minutes=minutes)
    payload = {
        "sub":   str(user_id),
        "sid":   str(session_id),
        "email": email,
        "tier":  tier,
        "jti":   jti,
        "iat":   int(_now().timestamp()),
        "exp":   int(expires_at.timestamp()),
        "iss":   "alphagraph",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


def verify_access_token(token: str) -> Optional[dict[str, Any]]:
    """Return the decoded payload or None if invalid / expired.

    Does NOT hit the database. The DB lookup happens in the FastAPI
    dependency that turns the payload into an AppUser instance.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="alphagraph",
            options={"require": ["exp", "iat", "sub", "sid"]},
        )
    except PyJWTError:
        return None
    return payload


def generate_refresh_token() -> tuple[str, str]:
    """Mint a new opaque refresh token. Returns (raw_token, sha256_hex_digest).

    The raw token is sent ONCE in the response to the OAuth callback (or
    the future /auth/refresh endpoint) — we never store it. The digest
    is stored in `oauth_session.refresh_token_hash` for later matching.
    """
    import hashlib
    raw = secrets.token_urlsafe(48)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest
