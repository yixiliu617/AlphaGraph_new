"""Phase 2 — user_note table for synced notes (OneNote, Google Keep, ...)

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-28

Per-user, per-source note pages. Initial provider: Microsoft OneNote
(`microsoft.onenote` service). Schema is general enough to hold Google
Keep notes too if/when Google opens that API publicly.

Schema:
  - title — page title; OneNote calls it "title"
  - notebook_id / notebook_name — OneNote organisational hierarchy
  - section_id / section_name — sections within a notebook
  - content_html — the page's HTML (text + inline images as data URIs +
    embedded handwriting). Capped via length-trim before insert; very
    large pages spill to nullable + `content_truncated=true`.
  - content_text — plaintext extracted from HTML for search.
  - created_at_remote / last_modified_at_remote — Microsoft's timestamps
    (the page lifecycle). `last_synced_at` tracks our local sync.
  - raw_payload — full Graph response JSON for forensic recovery.

Indexes:
  - (source_credential_id, source_note_id) UNIQUE — upsert key
  - (user_id, last_modified_at_remote) — hot path: "what's recent?"
  - GIN on tsvector(content_text) — added later when search ships
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_note",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_note_id",  sa.String(255), nullable=False),
        sa.Column("provider",        sa.String(32),  nullable=False),
        sa.Column("service",         sa.String(64),  nullable=False),

        sa.Column("title",           sa.Text(),      nullable=True),
        sa.Column("notebook_id",     sa.String(255), nullable=True),
        sa.Column("notebook_name",   sa.String(512), nullable=True),
        sa.Column("section_id",      sa.String(255), nullable=True),
        sa.Column("section_name",    sa.String(512), nullable=True),
        sa.Column("page_link",       sa.String(2048), nullable=True),
        sa.Column("content_html",    sa.Text(),      nullable=True),
        sa.Column("content_text",    sa.Text(),      nullable=True),
        sa.Column("content_truncated", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),

        sa.Column("created_at_remote",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modified_at_remote", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at",          sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at",              sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",              sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("raw_payload",             postgresql.JSONB(), nullable=True),

        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE",
                                name="fk_user_note_user"),
        sa.ForeignKeyConstraint(["source_credential_id"], ["user_credential.id"],
                                ondelete="CASCADE",
                                name="fk_user_note_credential"),
        sa.CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_note_provider",
        ),
        sa.UniqueConstraint(
            "source_credential_id", "source_note_id",
            name="uq_user_note_source",
        ),
    )
    op.create_index("ix_user_note_user_id", "user_note", ["user_id"])
    op.create_index("ix_user_note_user_modified",
                    "user_note", ["user_id", "last_modified_at_remote"])


def downgrade() -> None:
    op.drop_index("ix_user_note_user_modified", table_name="user_note")
    op.drop_index("ix_user_note_user_id",       table_name="user_note")
    op.drop_table("user_note")
