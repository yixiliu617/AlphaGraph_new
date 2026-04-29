"""Phase 2 — universe v2 schema (company, listing, thesis groups, pre-IPO, per-user)

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-29

Creates the Phase 2 universe schema. See `universe_v2_orm.py` for the
analytical rationale (why "v2", how it differs from the legacy
`public_universe`/`user_universe` tables in `universe_orm.py`).

Tables created (7):
  - company                — slug-keyed analytical entity
  - listing                — tradeable instrument; one company → many listings
  - universe_group         — thesis groups (ai_compute_design, ai_materials_*, ...)
  - universe_group_member  — many-to-many ticker × group with weight
  - pre_ipo_watch          — private companies tracked as metadata
  - user_universe_group    — per-user thesis-group subscriptions
  - user_universe_ticker   — per-user manual ticker adds

Partial unique indexes:
  - uq_listing_primary_per_company    — at most one primary listing per company
  - uq_ugm_primary_per_ticker         — at most one primary group per ticker

The seed data is loaded via `backend/scripts/seed_universe.py` after
upgrade. This migration creates EMPTY tables.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------- company ----------------
    op.create_table(
        "company",
        sa.Column("company_id",      sa.String(64),  primary_key=True),
        sa.Column("display_name",    sa.String(255), nullable=False),
        sa.Column("legal_name",      sa.String(255), nullable=True),
        sa.Column("hq_country",      sa.String(8),   nullable=True),
        sa.Column("fiscal_year_end", sa.String(8),   nullable=True),
        sa.Column("filings_source",  sa.String(32),  nullable=True),
        sa.Column("website",         sa.String(512), nullable=True),
        sa.Column("summary",         sa.Text(),      nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",      sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # ---------------- listing ----------------
    op.create_table(
        "listing",
        sa.Column("ticker",      sa.String(32), primary_key=True),
        sa.Column("company_id",  sa.String(64), nullable=False),
        sa.Column("exchange",    sa.String(32), nullable=False),
        sa.Column("currency",    sa.String(8),  nullable=False),
        sa.Column("is_primary",  sa.Boolean(),  nullable=False,
                  server_default=sa.text("false")),
        sa.Column("listed_at",   sa.Date(),     nullable=True),
        sa.Column("delisted_at", sa.Date(),     nullable=True),
        sa.Column("status",      sa.String(16), nullable=False,
                  server_default=sa.text("'active'")),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["company_id"], ["company.company_id"],
                                ondelete="CASCADE",
                                name="fk_listing_company"),
        sa.CheckConstraint(
            "status IN ('active', 'pre_ipo', 'recent_ipo', 'delisted', 'acquired')",
            name="ck_listing_status",
        ),
    )
    op.create_index("ix_listing_company_id", "listing", ["company_id"])
    op.create_index("ix_listing_status",     "listing", ["status"])
    # Partial unique: at most one is_primary=true per company.
    op.execute("""
        CREATE UNIQUE INDEX uq_listing_primary_per_company
        ON listing (company_id) WHERE is_primary = true
    """)

    # ---------------- universe_group ----------------
    op.create_table(
        "universe_group",
        sa.Column("group_id",     sa.String(64),  primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("description",  sa.Text(),      nullable=True),
        sa.Column("layer",        sa.String(32),  nullable=True),
        sa.Column("sort_order",   sa.Integer(),   nullable=False,
                  server_default=sa.text("999")),
        sa.Column("is_index",     sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
    )

    # ---------------- universe_group_member ----------------
    op.create_table(
        "universe_group_member",
        sa.Column("group_id",   sa.String(64),  nullable=False),
        sa.Column("ticker",     sa.String(32),  nullable=False),
        sa.Column("is_primary", sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("weight",     sa.Float(),     nullable=False,
                  server_default=sa.text("1.0")),
        sa.Column("notes",      sa.Text(),      nullable=True),
        sa.PrimaryKeyConstraint("group_id", "ticker", name="pk_ugm"),
        sa.ForeignKeyConstraint(["group_id"], ["universe_group.group_id"],
                                ondelete="CASCADE", name="fk_ugm_group"),
        sa.ForeignKeyConstraint(["ticker"], ["listing.ticker"],
                                ondelete="CASCADE", name="fk_ugm_listing"),
        sa.CheckConstraint("weight BETWEEN 0.0 AND 1.0",
                           name="ck_ugm_weight_range"),
    )
    op.create_index("ix_ugm_ticker", "universe_group_member", ["ticker"])
    op.execute("""
        CREATE UNIQUE INDEX uq_ugm_primary_per_ticker
        ON universe_group_member (ticker) WHERE is_primary = true
    """)

    # ---------------- pre_ipo_watch ----------------
    op.create_table(
        "pre_ipo_watch",
        sa.Column("id",                 sa.String(64),  primary_key=True),
        sa.Column("display_name",       sa.String(255), nullable=False),
        sa.Column("country",            sa.String(8),   nullable=True),
        sa.Column("category",           sa.String(64),  nullable=True),
        sa.Column("summary",            sa.Text(),      nullable=True),
        sa.Column("filings_status",     sa.String(255), nullable=True),
        sa.Column("expected_listing",   sa.String(128), nullable=True),
        sa.Column("expected_exchange",  sa.String(64),  nullable=True),
        sa.Column("last_round_payload", postgresql.JSONB(), nullable=True),
        sa.Column("tags",               postgresql.ARRAY(sa.String(64)), nullable=True),
        sa.Column("groups",             postgresql.ARRAY(sa.String(64)), nullable=True),
        sa.Column("post_ipo_ticker",    sa.String(32),  nullable=True),
        sa.Column("company_id",         sa.String(64),  nullable=True),
        sa.Column("created_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["company_id"], ["company.company_id"],
                                ondelete="SET NULL",
                                name="fk_pre_ipo_company"),
    )

    # ---------------- user_universe_group ----------------
    op.create_table(
        "user_universe_group",
        sa.Column("user_id",       postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id",      sa.String(64),                 nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True),    nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("display_order", sa.Integer(),                  nullable=True),
        sa.PrimaryKeyConstraint("user_id", "group_id", name="pk_uug"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE", name="fk_uug_user"),
        sa.ForeignKeyConstraint(["group_id"], ["universe_group.group_id"],
                                ondelete="CASCADE", name="fk_uug_group"),
    )

    # ---------------- user_universe_ticker ----------------
    op.create_table(
        "user_universe_ticker",
        sa.Column("user_id",   postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker",    sa.String(32),                 nullable=False),
        sa.Column("added_at",  sa.DateTime(timezone=True),    nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("source",    sa.String(64),                 nullable=False),
        sa.Column("is_pinned", sa.Boolean(),                  nullable=False,
                  server_default=sa.text("false")),
        sa.Column("notes",     sa.Text(),                     nullable=True),
        sa.PrimaryKeyConstraint("user_id", "ticker", name="pk_uut"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"],
                                ondelete="CASCADE", name="fk_uut_user"),
        sa.ForeignKeyConstraint(["ticker"], ["listing.ticker"],
                                ondelete="CASCADE", name="fk_uut_listing"),
    )
    op.create_index("ix_uut_user", "user_universe_ticker", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_uut_user", table_name="user_universe_ticker")
    op.drop_table("user_universe_ticker")
    op.drop_table("user_universe_group")
    op.drop_table("pre_ipo_watch")
    op.execute("DROP INDEX IF EXISTS uq_ugm_primary_per_ticker")
    op.drop_index("ix_ugm_ticker", table_name="universe_group_member")
    op.drop_table("universe_group_member")
    op.drop_table("universe_group")
    op.execute("DROP INDEX IF EXISTS uq_listing_primary_per_company")
    op.drop_index("ix_listing_status",     table_name="listing")
    op.drop_index("ix_listing_company_id", table_name="listing")
    op.drop_table("listing")
    op.drop_table("company")
