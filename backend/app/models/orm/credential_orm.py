"""
ORM model for `user_credential` — service-integration OAuth tokens.

Pairs with migration `0002_user_credential_for_service_integrations.py`.
See the migration file for the design rationale (why this is separate
from `oauth_session`, encryption choice, etc.).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index,
    LargeBinary, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.app.db.phase2_session import Phase2Base


class UserCredential(Phase2Base):
    __tablename__ = "user_credential"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE",
                   name="fk_user_credential_user"),
        nullable=False, index=True,
    )

    # Service identifier: "<provider>.<service>". See oauth_scopes.SERVICES.
    service  = Column(String(64), nullable=False, index=True)
    provider = Column(String(32), nullable=False)

    # The IdP's identity for the connected account. For Google: email.
    # For Microsoft: object ID (oid) or UPN.
    external_account_id    = Column(String(255), nullable=False)
    external_account_label = Column(String(255), nullable=True)

    # Encrypted token bytes (Fernet via auth.encryption).
    access_token_encrypted  = Column(LargeBinary, nullable=True)
    refresh_token_encrypted = Column(LargeBinary, nullable=True)
    access_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Granted scopes from the IdP's token response.
    scopes = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    # Sync state.
    sync_enabled     = Column(Boolean, nullable=False, server_default=text("true"))
    last_synced_at   = Column(DateTime(timezone=True), nullable=True)
    last_sync_cursor = Column(Text,    nullable=True)
    last_sync_error  = Column(Text,    nullable=True)

    # Lifecycle.
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("AppUser", backref="credentials")

    __table_args__ = (
        CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_credential_provider",
        ),
        UniqueConstraint(
            "user_id", "service", "external_account_id",
            name="uq_user_credential_account",
        ),
        Index(
            "ix_user_credential_sync_active",
            "service", "last_synced_at",
            postgresql_where=text("sync_enabled = true AND revoked_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (f"<UserCredential {self.service} "
                f"{self.external_account_id} user={self.user_id}>")
