"""
ORM models for the Phase 2 universe schema — companies, listings,
thesis groups, pre-IPO watch, and per-user universe state.

Why "v2": the legacy `universe_orm.py` defines `public_universe` +
`user_universe` on the old Base (legacy SQLite/Postgres). Those tables
serve the old tenant-scoped catalogue. The Phase 2 universe is a
different design — thesis-group-driven, multi-listing, with pre-IPO
tracking — and lives on `Phase2Base`. Names are deliberately distinct
(`company`, `listing`, `universe_group_*`, `user_universe_group`,
`user_universe_ticker`) so both schemas can coexist on the same
Postgres without collision.

Tables:
  - company             — analytical entity (TSMC, Alibaba, ...)
  - listing             — tradeable instrument (BABA, 9988.HK, ...)
                          one company → many listings
  - universe_group      — thesis groups (ai_compute_design, ...)
  - universe_group_member — many-to-many ticker × group with weight
  - pre_ipo_watch       — private companies tracked as metadata
  - user_universe_group — per-user group subscriptions
  - user_universe_ticker — per-user manual ticker adds (or removals
                            via tombstone + is_pinned)
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from backend.app.db.phase2_session import Phase2Base


class Company(Phase2Base):
    """Analytical unit. Slug-keyed (`tsmc`, `alibaba`, `tokyo_electron`).
    Dual-listings (BABA + 9988.HK) point to the SAME company_id."""
    __tablename__ = "company"

    company_id      = Column(String(64),  primary_key=True)
    display_name    = Column(String(255), nullable=False)
    legal_name      = Column(String(255), nullable=True)
    hq_country      = Column(String(8),   nullable=True)   # 'TW', 'CN', 'US', 'JP', 'KR', ...
    fiscal_year_end = Column(String(8),   nullable=True)   # 'Dec', 'Mar', 'Jun', 'Sep'
    filings_source  = Column(String(32),  nullable=True)   # 'sec_10k' | 'sec_20f' | 'hkex' | 'tdnet' | 'mops' | 'dart'
    website         = Column(String(512), nullable=True)
    summary         = Column(Text,        nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))

    listings = relationship("Listing", back_populates="company",
                            cascade="all, delete-orphan")


class Listing(Phase2Base):
    """A tradeable instrument. Ticker is the natural key (yfinance
    convention: 'BABA', '9988.HK', '2330.TW', '8035.T', '005930.KS')."""
    __tablename__ = "listing"

    ticker      = Column(String(32),  primary_key=True)
    company_id  = Column(String(64),
                         ForeignKey("company.company_id",
                                    ondelete="CASCADE",
                                    name="fk_listing_company"),
                         nullable=False, index=True)
    exchange    = Column(String(32),  nullable=False)   # 'NYSE', 'NASDAQ', 'TWSE', 'HKEX', 'JPX', 'KOSPI', 'SSE', 'SZSE', ...
    currency    = Column(String(8),   nullable=False)   # 'USD', 'TWD', 'HKD', 'JPY', 'KRW', 'CNY', 'EUR', ...
    is_primary  = Column(Boolean,     nullable=False, server_default=text("false"))
    listed_at   = Column(Date,        nullable=True)
    delisted_at = Column(Date,        nullable=True)
    status      = Column(String(16),  nullable=False, server_default=text("'active'"))

    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))

    company = relationship("Company", back_populates="listings")
    group_memberships = relationship("UniverseGroupMember",
                                     back_populates="listing",
                                     cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'pre_ipo', 'recent_ipo', 'delisted', 'acquired')",
            name="ck_listing_status",
        ),
        Index("ix_listing_status", "status"),
        # Partial unique index: each company has at most one primary listing.
        # Created in the migration with WHERE clause; declarative form below
        # for autogen reflection only — Postgres applies the WHERE filter.
        Index("uq_listing_primary_per_company", "company_id",
              unique=True, postgresql_where=text("is_primary = true")),
    )


class UniverseGroup(Phase2Base):
    """Thesis groups: 'ai_compute_design', 'ai_materials_critical_minerals',
    'index_smh', etc. The set is small (~30) and stable; rows live in
    code but mirrored to DB so foreign keys work."""
    __tablename__ = "universe_group"

    group_id     = Column(String(64),  primary_key=True)
    display_name = Column(String(128), nullable=False)
    description  = Column(Text,        nullable=True)
    layer        = Column(String(32),  nullable=True)
    sort_order   = Column(Integer,     nullable=False, server_default=text("999"))
    is_index     = Column(Boolean,     nullable=False, server_default=text("false"))

    members = relationship("UniverseGroupMember",
                           back_populates="group",
                           cascade="all, delete-orphan")


class UniverseGroupMember(Phase2Base):
    """Many-to-many: ticker × group. A ticker can be in 2+ groups
    (NVDA in compute_design + software_models). is_primary picks the
    "headline" group for UI labeling. weight is 0.0–1.0 (pure-play =
    1.0, adjacent = 0.3); the heatmap can sort by it."""
    __tablename__ = "universe_group_member"

    group_id   = Column(String(64),
                        ForeignKey("universe_group.group_id",
                                   ondelete="CASCADE",
                                   name="fk_ugm_group"),
                        primary_key=True)
    ticker     = Column(String(32),
                        ForeignKey("listing.ticker",
                                   ondelete="CASCADE",
                                   name="fk_ugm_listing"),
                        primary_key=True)
    is_primary = Column(Boolean, nullable=False, server_default=text("false"))
    weight     = Column(Float,   nullable=False, server_default=text("1.0"))
    notes      = Column(Text,    nullable=True)

    group   = relationship("UniverseGroup", back_populates="members")
    listing = relationship("Listing",       back_populates="group_memberships")

    __table_args__ = (
        Index("ix_ugm_ticker", "ticker"),
        # Partial unique index: each ticker has at most one is_primary=true row.
        Index("uq_ugm_primary_per_ticker", "ticker",
              unique=True, postgresql_where=text("is_primary = true")),
        CheckConstraint("weight BETWEEN 0.0 AND 1.0", name="ck_ugm_weight_range"),
    )


class PreIPOWatch(Phase2Base):
    """Private companies tracked as metadata-only watch entries.
    `post_ipo_ticker` is set when the IPO happens; status flips to
    'recent_ipo' and we backfill prices on the new ticker."""
    __tablename__ = "pre_ipo_watch"

    id                 = Column(String(64),  primary_key=True)
    display_name       = Column(String(255), nullable=False)
    country            = Column(String(8),   nullable=True)
    category           = Column(String(64),  nullable=True)
    summary            = Column(Text,        nullable=True)
    filings_status     = Column(String(255), nullable=True)
    expected_listing   = Column(String(128), nullable=True)
    expected_exchange  = Column(String(64),  nullable=True)
    last_round_payload = Column(JSONB,       nullable=True)
    tags               = Column(ARRAY(String(64)), nullable=True)
    groups             = Column(ARRAY(String(64)), nullable=True)
    post_ipo_ticker    = Column(String(32),  nullable=True)
    company_id         = Column(String(64),
                                ForeignKey("company.company_id",
                                           ondelete="SET NULL",
                                           name="fk_pre_ipo_company"),
                                nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))


class UserUniverseGroup(Phase2Base):
    """Per-user thesis-group subscriptions. The user's universe is
    the UNION of these groups' members + their manual ticker adds."""
    __tablename__ = "user_universe_group"

    user_id       = Column(UUID(as_uuid=True),
                           ForeignKey("app_user.id", ondelete="CASCADE",
                                      name="fk_uug_user"),
                           primary_key=True)
    group_id      = Column(String(64),
                           ForeignKey("universe_group.group_id",
                                      ondelete="CASCADE",
                                      name="fk_uug_group"),
                           primary_key=True)
    subscribed_at = Column(DateTime(timezone=True), nullable=False,
                           server_default=text("CURRENT_TIMESTAMP"))
    display_order = Column(Integer, nullable=True)


class UserUniverseTicker(Phase2Base):
    """Per-user manual ticker adds (and override pins). Sourced as
    'manual' for hand-picked names, 'preset:<group_id>' if the row
    was materialised from a group subscription, or 'auto_promoted'
    if the auto-promotion flow added a previously-unknown name."""
    __tablename__ = "user_universe_ticker"

    user_id   = Column(UUID(as_uuid=True),
                       ForeignKey("app_user.id", ondelete="CASCADE",
                                  name="fk_uut_user"),
                       primary_key=True)
    ticker    = Column(String(32),
                       ForeignKey("listing.ticker", ondelete="CASCADE",
                                  name="fk_uut_listing"),
                       primary_key=True)
    added_at  = Column(DateTime(timezone=True), nullable=False,
                       server_default=text("CURRENT_TIMESTAMP"))
    source    = Column(String(64),  nullable=False)
    is_pinned = Column(Boolean,     nullable=False, server_default=text("false"))
    notes     = Column(Text,        nullable=True)

    __table_args__ = (
        Index("ix_uut_user", "user_id"),
    )
