"""
ORM models for Phase 2 user / auth tables.

Pairs with migration `0001_phase2_user_alert_heartbeat.py`. Schema is
intentionally lightweight — UUIDs everywhere, CHECK constraints for the
small enums (provider, tier, status), no Postgres ENUM types so adding
a new tier or status later is a simple CHECK-constraint recreate, not
a multi-step enum-rebuild.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.app.db.phase2_session import Phase2Base


class AppUser(Phase2Base):
    __tablename__ = "app_user"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    email            = Column(String(320), nullable=False, unique=True, index=True)
    name             = Column(String(255), nullable=True)
    oauth_provider   = Column(String(32),  nullable=False)
    oauth_subject_id = Column(String(255), nullable=False)
    tier             = Column(String(32),  nullable=False, server_default=text("'free'"))
    admin_role       = Column(String(16),  nullable=False, server_default=text("'user'"))
    is_active        = Column(Boolean,     nullable=False, server_default=text("true"))
    created_at       = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_seen_at     = Column(DateTime(timezone=True), nullable=True)

    sessions = relationship(
        "OAuthSession", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    alerts = relationship(
        "UserAlert", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "oauth_provider IN ('google', 'microsoft')",
            name="ck_app_user_oauth_provider",
        ),
        CheckConstraint(
            "tier IN ('free', 'pro', 'institutional')",
            name="ck_app_user_tier",
        ),
        UniqueConstraint(
            "oauth_provider", "oauth_subject_id",
            name="uq_app_user_oauth_subject",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AppUser {self.email} {self.oauth_provider}/{self.tier}>"


class OAuthSession(Phase2Base):
    """A logged-in user session.

    `refresh_token_hash` stores a sha256 hex digest of the raw refresh
    token — never the raw token itself. The session cookie carries a
    short-lived JWT (access token); the refresh path matches the JWT's
    session_id claim against `oauth_session.id` and the hash to confirm
    the refresh attempt is legitimate.
    """
    __tablename__ = "oauth_session"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE", name="fk_oauth_session_user"),
        nullable=False, index=True,
    )
    refresh_token_hash = Column(String(64), nullable=False, unique=True)
    access_token_jti   = Column(String(64), nullable=True)
    expires_at         = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at         = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    revoked_at         = Column(DateTime(timezone=True), nullable=True)
    ip_first           = Column(String(45), nullable=True)
    ip_last            = Column(String(45), nullable=True)
    user_agent         = Column(String(512), nullable=True)

    user = relationship("AppUser", back_populates="sessions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OAuthSession user={self.user_id} expires={self.expires_at}>"


# Side-effect imports: ensure SQLAlchemy can resolve the string references
# in `AppUser.relationship("UserAlert")` and any future cross-module
# relationships when mappers are first configured. SQLAlchemy resolves
# relationship() string targets lazily on first query, so all related
# classes must be present in the same registry by then. Done at the end of
# this module to avoid circular imports — AppUser / OAuthSession are fully
# defined above, and the dependent modules only import Phase2Base (already
# loaded via this module's own import).
from backend.app.models.orm import alert_orm       # noqa: F401, E402
from backend.app.models.orm import credential_orm  # noqa: F401, E402
