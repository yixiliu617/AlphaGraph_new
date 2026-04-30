"""user onboarding (waitlist + profile + GICS)

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-30

Tables created (6):
  - waitlist_entry   — pre-launch waitlist with status workflow
  - gics_sector      — GICS sector catalogue + synthetic rows (seeded)
  - user_profile     — onboarding wizard state + role/strategy metadata
  - user_sector      — per-user sector coverage selections
  - user_country     — per-user country coverage selections
  - user_theme       — per-user free-text investment theme entries

Also:
  - CREATE EXTENSION citext  (case-insensitive text for waitlist_entry.email)
  - ALTER TABLE app_user ADD COLUMN admin_role
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # citext = case-insensitive text. Used for waitlist_entry.email so
    # b@x.com == B@X.com on uniqueness.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ---- ALTER app_user — add admin_role ----
    op.add_column(
        "app_user",
        sa.Column("admin_role", sa.String(16), nullable=False,
                  server_default=sa.text("'user'")),
    )
    op.create_check_constraint(
        "ck_app_user_admin_role",
        "app_user",
        "admin_role IN ('user', 'admin')",
    )

    # ---- waitlist_entry ----
    op.execute("""
        CREATE TABLE waitlist_entry (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                    CITEXT UNIQUE NOT NULL,
            full_name                VARCHAR(255),
            self_reported_role       VARCHAR(64),
            self_reported_firm       VARCHAR(255),
            note                     TEXT,
            referrer                 VARCHAR(255),
            referred_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
            status                   VARCHAR(32) NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending','approved','rejected','self_serve_attempt')),
            requested_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_at              TIMESTAMPTZ,
            approved_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
            rejected_reason          TEXT,
            invite_email_sent_at     TIMESTAMPTZ,
            invite_email_clicked_at  TIMESTAMPTZ
        )
    """)
    op.create_index("ix_waitlist_status_requested",
                    "waitlist_entry", ["status", "requested_at"])

    # ---- gics_sector ----
    op.create_table(
        "gics_sector",
        sa.Column("id",                sa.String(64),  primary_key=True),
        sa.Column("parent_sector_id",  sa.String(64),
                  sa.ForeignKey("gics_sector.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("display_name",      sa.String(128), nullable=False),
        sa.Column("is_industry_group", sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_synthetic",      sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("sort_order",        sa.Integer(),   nullable=False,
                  server_default=sa.text("0")),
    )

    # ---- user_profile ----
    op.create_table(
        "user_profile",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("role",                 sa.String(32),  nullable=True),
        sa.Column("role_other",           sa.String(255), nullable=True),
        sa.Column("firm_strategy",        sa.String(32),  nullable=True),
        sa.Column("firm_strategy_other",  sa.String(255), nullable=True),
        sa.Column("firm_name",            sa.String(255), nullable=True),
        sa.Column("is_generalist",        sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("wizard_current_step",  sa.Integer(),   nullable=False,
                  server_default=sa.text("1")),
        sa.Column("wizard_completed_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("wizard_skipped_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("wizard_current_step BETWEEN 1 AND 6",
                           name="ck_user_profile_step"),
    )

    # ---- user_sector ----
    op.create_table(
        "user_sector",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("sector_id", sa.String(64),
                  sa.ForeignKey("gics_sector.id"), primary_key=True),
        sa.Column("custom_label", sa.String(255), nullable=True),
        sa.Column("selected_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # ---- user_country ----
    op.create_table(
        "user_country",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("country_code", sa.String(8), primary_key=True),
        sa.Column("custom_label", sa.String(255), nullable=True),
        sa.Column("selected_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # ---- user_theme ----
    op.create_table(
        "user_theme",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sector_id", sa.String(64),
                  sa.ForeignKey("gics_sector.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("theme_text", sa.Text(),    nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_user_theme_user_sector",
                    "user_theme", ["user_id", "sector_id"])

    # ---- Seed gics_sector (18 rows: 14 selectable + 2 parent groupings + 2 synthetic) ----
    op.execute("""
        INSERT INTO gics_sector (id, parent_sector_id, display_name, is_industry_group, is_synthetic, sort_order) VALUES
          ('information_technology',    NULL, 'Information Technology',     false, false, 0),
          ('communication_services',    NULL, 'Communication Services',     false, false, 0),
          ('energy',                    NULL, 'Energy',                     false, false, 100),
          ('materials',                 NULL, 'Materials',                  false, false, 110),
          ('industrials',               NULL, 'Industrials',                false, false, 120),
          ('consumer_discretionary',    NULL, 'Consumer Discretionary',     false, false, 130),
          ('consumer_staples',          NULL, 'Consumer Staples',           false, false, 140),
          ('health_care',               NULL, 'Health Care',                false, false, 150),
          ('financials',                NULL, 'Financials',                 false, false, 160),
          ('semiconductors_eq',         'information_technology', 'Semiconductors & Equipment', true, false, 170),
          ('tech_hardware_eq',          'information_technology', 'Tech Hardware & Equipment',  true, false, 180),
          ('software_services',         'information_technology', 'Software & Services',        true, false, 190),
          ('telecom_services',          'communication_services', 'Telecom Services',           true, false, 200),
          ('media_entertainment',       'communication_services', 'Media & Entertainment',      true, false, 210),
          ('utilities',                 NULL, 'Utilities',                  false, false, 220),
          ('real_estate',               NULL, 'Real Estate',                false, false, 230),
          ('generalist',                NULL, 'Generalist',                 false, true,  240),
          ('other',                     NULL, 'Other',                      false, true,  250)
    """)


def downgrade() -> None:
    op.drop_index("ix_user_theme_user_sector", table_name="user_theme")
    op.drop_table("user_theme")
    op.drop_table("user_country")
    op.drop_table("user_sector")
    op.drop_table("user_profile")
    op.drop_table("gics_sector")
    op.drop_index("ix_waitlist_status_requested", table_name="waitlist_entry")
    op.execute("DROP TABLE waitlist_entry")
    op.drop_constraint("ck_app_user_admin_role", "app_user", type_="check")
    op.drop_column("app_user", "admin_role")
    # Don't drop the citext extension — other tables may still need it.
