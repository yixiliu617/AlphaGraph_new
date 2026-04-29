"""Phase 2 — user_calendar_event table for synced calendar events

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-28

Per-user, per-source calendar events. Synced from Google Calendar /
Microsoft Graph by the `integrations` package's sync runner.

Why a single cross-provider table:
  - Querying "all my upcoming meetings" should hit one index, not fan
    out to per-provider tables.
  - The `source_credential_id` + `source_event_id` pair tells you where
    each row came from.
  - When the user disconnects Google Calendar but keeps Outlook, the
    Google rows can be soft-deleted by the sync runner (or hard-deleted
    via `ON DELETE CASCADE` on `source_credential_id`) without touching
    Outlook rows.

Schema:
  - title / description / location / html_link — display fields
  - start_at / end_at — timezone-aware UTC. all_day flagged separately
    so the UI can render an all-day pill instead of "12:00 AM".
  - attendees / organizer — JSONB. Provider shapes differ slightly
    (Google: response_status, Microsoft: status); we normalise on write
    to {email, name, response_status} per attendee.
  - status — confirmed / tentative / cancelled (Google's "cancelled"
    rows are tombstones for previously-synced events the user removed).
  - recurrence_master_id — for instances of a recurring event, points
    at the master event's source_event_id. Lets us collapse "every
    weekday" into one card if we want.
  - last_modified_at — provider-side. Used to detect changes during
    incremental sync.
  - raw_payload — full provider JSON. Optional but cheap insurance:
    if the upstream changes a field shape, we can re-derive without a
    re-fetch.

Indexes:
  - (source_credential_id, source_event_id) UNIQUE — upsert key
  - (user_id, start_at) — hot path for "next N days"
  - (user_id, status) WHERE status='confirmed' — partial, for the
    common dashboard query that excludes tentative/cancelled
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_calendar_event",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_event_id",   sa.String(255), nullable=False),
        sa.Column("source_calendar_id", sa.String(255), nullable=True),
        sa.Column("provider",           sa.String(32),  nullable=False),
        sa.Column("title",              sa.Text(),      nullable=True),
        sa.Column("description",        sa.Text(),      nullable=True),
        sa.Column("location",           sa.String(512), nullable=True),
        sa.Column("html_link",          sa.String(1024), nullable=True),
        sa.Column("start_at",           sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at",             sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day",            sa.Boolean(),   nullable=False, server_default=sa.text("false")),
        sa.Column("attendees",          postgresql.JSONB(), nullable=True),
        sa.Column("organizer",          postgresql.JSONB(), nullable=True),
        sa.Column("status",             sa.String(16),  nullable=False, server_default=sa.text("'confirmed'")),
        sa.Column("recurrence_master_id", sa.String(255), nullable=True),
        sa.Column("last_modified_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at",     sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("raw_payload",        postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE",
                                name="fk_user_calendar_event_user"),
        sa.ForeignKeyConstraint(["source_credential_id"], ["user_credential.id"],
                                ondelete="CASCADE",
                                name="fk_user_calendar_event_credential"),
        sa.CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_calendar_event_provider",
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'tentative', 'cancelled')",
            name="ck_user_calendar_event_status",
        ),
        sa.UniqueConstraint(
            "source_credential_id", "source_event_id",
            name="uq_user_calendar_event_source",
        ),
    )
    op.create_index("ix_user_calendar_event_user_id",
                    "user_calendar_event", ["user_id"])
    op.create_index("ix_user_calendar_event_user_start",
                    "user_calendar_event", ["user_id", "start_at"])
    op.create_index("ix_user_calendar_event_user_status_active",
                    "user_calendar_event", ["user_id", "start_at"],
                    postgresql_where=sa.text("status = 'confirmed'"))


def downgrade() -> None:
    op.drop_index("ix_user_calendar_event_user_status_active",
                  table_name="user_calendar_event")
    op.drop_index("ix_user_calendar_event_user_start",
                  table_name="user_calendar_event")
    op.drop_index("ix_user_calendar_event_user_id",
                  table_name="user_calendar_event")
    op.drop_table("user_calendar_event")
