"""Phase 2 — initial user/auth/alert/heartbeat tables

Revision ID: 0001
Revises:
Create Date: 2026-04-28

This is the FIRST migration in the Phase 2 storage refactor. It introduces:

  - `app_user`         — user accounts (Google / Microsoft OAuth)
  - `oauth_session`    — refresh tokens + session metadata
  - `user_alert`       — TA-style price-level / rule-DSL alerts
  - `scraper_heartbeat`— cross-domain replacement for the existing
                        `taiwan_scraper_heartbeat` SQLite table

Design notes:

  - All primary keys use UUIDs (server-generated). Avoids leaking row counts
    and sidesteps the integer-PK race issue that bit us in the news cluster
    table earlier this quarter.
  - `app_user.tier` follows locked decision D9 (free / pro / institutional)
    and is enforced via a CHECK constraint, not a Postgres ENUM, so adding
    a tier later is a CHECK-recreate, not an enum-rebuild.
  - `oauth_session` stores only a HASH of the refresh token (sha256-hex),
    never the raw token. Same pattern we'll use for any long-lived secret.
  - `user_alert.condition_dsl` is the raw rule string the user typed
    (e.g. `NVDA close > 200`). `condition_parsed` is the JSONB parsed form
    the evaluator runs against. We keep both: the raw text for display,
    the parsed form for execution. Mismatch is a re-parse signal.
  - `scraper_heartbeat` is intentionally a rename of the existing
    `taiwan_scraper_heartbeat` table — that name was historical artefact
    (the table was already cross-domain by the time prices/social joined).
    Data migration from the SQLite table is a one-shot script (see
    `backend/scripts/migrations/0001_copy_heartbeats_from_sqlite.py`),
    NOT part of this DDL migration.

After review + apply, downstream pieces:
  - B2 (OAuth): writes to app_user + oauth_session
  - Phase 3D (rule DSL alerts): writes to user_alert
  - Existing schedulers: switch their connection from the SQLite file to
    Postgres via the same settings.POSTGRES_URI, write to scraper_heartbeat
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # app_user — user accounts (Google + Microsoft OAuth, decision D10)
    # -----------------------------------------------------------------
    op.create_table(
        "app_user",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("oauth_provider", sa.String(32), nullable=False),
        sa.Column("oauth_subject_id", sa.String(255), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False,
                  server_default=sa.text("'free'")),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "oauth_provider IN ('google', 'microsoft')",
            name="ck_app_user_oauth_provider",
        ),
        sa.CheckConstraint(
            "tier IN ('free', 'pro', 'institutional')",
            name="ck_app_user_tier",
        ),
        sa.UniqueConstraint("oauth_provider", "oauth_subject_id",
                            name="uq_app_user_oauth_subject"),
    )
    op.create_index("ix_app_user_email", "app_user", ["email"], unique=True)

    # -----------------------------------------------------------------
    # oauth_session — refresh-token-hash + session metadata
    # -----------------------------------------------------------------
    op.create_table(
        "oauth_session",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("access_token_jti", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_first", sa.String(45), nullable=True),
        sa.Column("ip_last", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE",
                                name="fk_oauth_session_user"),
        sa.UniqueConstraint("refresh_token_hash",
                            name="uq_oauth_session_refresh_hash"),
    )
    op.create_index("ix_oauth_session_user_id", "oauth_session", ["user_id"])
    op.create_index("ix_oauth_session_expires_at", "oauth_session", ["expires_at"])

    # -----------------------------------------------------------------
    # user_alert — TA-style alerts on price + indicator levels
    # -----------------------------------------------------------------
    # Holds BOTH the raw DSL string the user wrote AND the parsed JSONB
    # form the evaluator runs against. The evaluator hits this table on
    # every price tick (see Phase 3D plan).
    op.create_table(
        "user_alert",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("condition_dsl", sa.Text(), nullable=False),
        sa.Column("condition_parsed", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_value", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE",
                                name="fk_user_alert_user"),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'triggered', 'expired', 'archived')",
            name="ck_user_alert_status",
        ),
    )
    op.create_index("ix_user_alert_user_id", "user_alert", ["user_id"])
    op.create_index("ix_user_alert_status", "user_alert", ["status"])
    # Hot-path index: the evaluator scans active alerts per ticker.
    op.create_index("ix_user_alert_ticker_active",
                    "user_alert", ["ticker", "status"],
                    postgresql_where=sa.text("status = 'active'"))

    # -----------------------------------------------------------------
    # scraper_heartbeat — cross-domain replacement for taiwan_scraper_heartbeat
    # -----------------------------------------------------------------
    # Same shape as the existing SQLite table (taiwan/health.py) so a copy
    # script can do INSERT...SELECT with no transformation. Renamed because
    # the table is cross-domain (taiwan + social + prices all write here).
    op.create_table(
        "scraper_heartbeat",
        sa.Column("scraper_name", sa.String(128), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_msg", sa.Text(), nullable=True),
        sa.Column("rows_inserted", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("rows_updated", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("rows_amended", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "status IN ('ok', 'degraded', 'failed')",
            name="ck_scraper_heartbeat_status",
        ),
    )

    # pgcrypto for gen_random_uuid(). Postgres 13+ has it as a built-in
    # extension; CREATE EXTENSION IF NOT EXISTS is a no-op when present.
    # (Run separately if your role lacks superuser; the docker-compose user
    # has the permission.)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.drop_table("scraper_heartbeat")
    op.drop_index("ix_user_alert_ticker_active", table_name="user_alert")
    op.drop_index("ix_user_alert_status", table_name="user_alert")
    op.drop_index("ix_user_alert_user_id", table_name="user_alert")
    op.drop_table("user_alert")
    op.drop_index("ix_oauth_session_expires_at", table_name="oauth_session")
    op.drop_index("ix_oauth_session_user_id", table_name="oauth_session")
    op.drop_table("oauth_session")
    op.drop_index("ix_app_user_email", table_name="app_user")
    op.drop_table("app_user")
    # Don't drop pgcrypto — other apps may depend on it.
