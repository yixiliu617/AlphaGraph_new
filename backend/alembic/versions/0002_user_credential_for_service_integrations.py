"""Phase 2 — user_credential table for per-service OAuth tokens

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-28

Adds the `user_credential` table — separate from `oauth_session`, which
only holds login-session refresh-token hashes. `user_credential` holds
the FULL OAuth credentials we need to call third-party APIs as the
user (Google Calendar, Outlook Mail, OneDrive, etc.).

Why separate from `oauth_session`:
  - `oauth_session` is "is this user logged in to AlphaGraph?". Has
    one row per login session per device. Stores only a sha256 hash.
  - `user_credential` is "what services has this user connected for
    background sync?". Has one row per (user, service). Stores the
    raw refresh token (Fernet-encrypted at rest) so we can refresh
    access tokens without user interaction.

A user might have:
  - 1 oauth_session (logged in via Google sign-in)
  - 1 user_credential for "google.calendar" (connected Calendar later)
  - 1 user_credential for "microsoft.outlook_mail" (connected Outlook)
  - 1 user_credential for "microsoft.onedrive"

These are independent connections — disconnecting Calendar doesn't log
the user out. Sign-in identity vs. service connections.

Encryption:
  - access_token_encrypted, refresh_token_encrypted: Fernet bytes.
  - The Fernet key is in env var TOKEN_ENCRYPTION_KEY (32 url-safe
    base64 bytes). Must be present in any environment that decrypts.
  - Why Fernet over pgcrypto: portable across SQLite/Postgres + simple
    key rotation (re-encrypt with a new key, update env var).

Sync state:
  - last_synced_at: when the worker last finished a successful pull
  - last_sync_cursor: opaque per-provider sync cursor (Google
    syncToken, Microsoft delta link, etc.)
  - sync_enabled: user toggle to pause sync without disconnecting.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Service identifier: "<provider>.<service>" — e.g.
        # "google.calendar", "google.gmail", "microsoft.outlook_mail",
        # "microsoft.onedrive", "microsoft.onenote", "microsoft.calendar".
        sa.Column("service",  sa.String(64),  nullable=False),
        sa.Column("provider", sa.String(32),  nullable=False),
        # External account this credential authenticates as. For Google,
        # this is the user's email at the IdP (which can differ from
        # AppUser.email — e.g. user signed up with personal Gmail but
        # connected Calendar from a work Workspace account). For
        # Microsoft it's the OID (object ID) or UPN.
        sa.Column("external_account_id",    sa.String(255), nullable=False),
        sa.Column("external_account_label", sa.String(255), nullable=True),
        # Encrypted token bytes (Fernet).
        sa.Column("access_token_encrypted",  sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        # Granted scopes, as the IdP returned them. Stored as JSON-array
        # so we can ALTER the granted scopes via re-consent without
        # changing the schema.
        sa.Column("scopes", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        # Sync state.
        sa.Column("sync_enabled",     sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_cursor", sa.Text(),    nullable=True),
        sa.Column("last_sync_error",  sa.Text(),    nullable=True),
        # Lifecycle.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE",
                                name="fk_user_credential_user"),
        sa.CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_credential_provider",
        ),
        # One credential per (user, service, external_account_id) —
        # users can connect multiple Google accounts to Calendar.
        sa.UniqueConstraint(
            "user_id", "service", "external_account_id",
            name="uq_user_credential_account",
        ),
    )
    op.create_index("ix_user_credential_user_id",
                    "user_credential", ["user_id"])
    op.create_index("ix_user_credential_service",
                    "user_credential", ["service"])
    # Hot-path index for the sync worker: every tick scans active
    # credentials with sync_enabled=true and last_synced_at older than
    # the per-service interval.
    op.create_index("ix_user_credential_sync_active",
                    "user_credential", ["service", "last_synced_at"],
                    postgresql_where=sa.text("sync_enabled = true AND revoked_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_user_credential_sync_active", table_name="user_credential")
    op.drop_index("ix_user_credential_service",     table_name="user_credential")
    op.drop_index("ix_user_credential_user_id",     table_name="user_credential")
    op.drop_table("user_credential")
