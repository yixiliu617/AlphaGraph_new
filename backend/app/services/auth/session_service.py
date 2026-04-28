"""
DB-side session helpers for OAuth.

`upsert_user_from_idtoken` — given the OIDC claims (`sub`, `email`,
`name`, `provider`), look up or create the AppUser row and update
`last_seen_at`.

`create_session` — insert an OAuthSession row for the user. Returns the
SQLAlchemy instance + the raw refresh token string (only this once).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.models.orm.user_orm import AppUser, OAuthSession
from backend.app.services.auth.jwt_token import generate_refresh_token


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upsert_user_from_idtoken(
    db: Session,
    *,
    provider: str,
    subject_id: str,
    email: str,
    name: Optional[str] = None,
) -> AppUser:
    """Look up an AppUser by (provider, subject_id) or create one. Bumps
    last_seen_at. Returns the user (committed)."""
    user = (
        db.query(AppUser)
        .filter(
            AppUser.oauth_provider == provider,
            AppUser.oauth_subject_id == subject_id,
        )
        .first()
    )
    if user is None:
        user = AppUser(
            email=email,
            name=name,
            oauth_provider=provider,
            oauth_subject_id=subject_id,
        )
        db.add(user)
        db.flush()
    else:
        # Refresh whatever the IdP knows about the user — email can change.
        if user.email != email:
            user.email = email
        if name and user.name != name:
            user.name = name
    user.last_seen_at = _now()
    db.commit()
    db.refresh(user)
    return user


def create_session(
    db: Session,
    *,
    user: AppUser,
    refresh_ttl_days: int = 30,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[OAuthSession, str]:
    """Insert a fresh OAuthSession. Returns (orm_row, raw_refresh_token).

    Caller mints the JWT access token separately (jwt_token.issue_access_token)
    and uses that token's jti on the row via `attach_access_token_jti`.
    """
    raw, digest = generate_refresh_token()
    session = OAuthSession(
        user_id=user.id,
        refresh_token_hash=digest,
        expires_at=_now() + timedelta(days=refresh_ttl_days),
        ip_first=ip,
        ip_last=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, raw


def attach_access_token_jti(
    db: Session, *, session: OAuthSession, jti: str,
) -> None:
    """Record the jti of the most recently issued access token on this session.
    Lets us revoke a specific access token by clearing this column."""
    session.access_token_jti = jti
    db.commit()


def revoke_session(db: Session, *, session: OAuthSession) -> None:
    session.revoked_at = _now()
    db.commit()


def find_active_session(
    db: Session, *, session_id, user_id,
) -> Optional[OAuthSession]:
    return (
        db.query(OAuthSession)
        .filter(
            OAuthSession.id == session_id,
            OAuthSession.user_id == user_id,
            OAuthSession.revoked_at.is_(None),
            OAuthSession.expires_at > _now(),
        )
        .first()
    )
