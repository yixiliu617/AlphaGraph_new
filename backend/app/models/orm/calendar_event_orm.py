"""
ORM model for `user_calendar_event` — synced calendar events.

Pairs with migration `0003_user_calendar_event.py`. See the migration
for design rationale (one cross-provider table, raw_payload kept for
forensic recovery, partial index on confirmed status).
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.app.db.phase2_session import Phase2Base


class UserCalendarEvent(Phase2Base):
    __tablename__ = "user_calendar_event"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE",
                   name="fk_user_calendar_event_user"),
        nullable=False, index=True,
    )
    source_credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("user_credential.id", ondelete="CASCADE",
                   name="fk_user_calendar_event_credential"),
        nullable=False,
    )
    source_event_id    = Column(String(255), nullable=False)
    source_calendar_id = Column(String(255), nullable=True)
    provider           = Column(String(32),  nullable=False)
    title              = Column(Text,        nullable=True)
    description        = Column(Text,        nullable=True)
    location           = Column(String(512), nullable=True)
    html_link          = Column(String(1024), nullable=True)
    start_at           = Column(DateTime(timezone=True), nullable=False)
    end_at             = Column(DateTime(timezone=True), nullable=True)
    all_day            = Column(Boolean,     nullable=False, server_default=text("false"))
    attendees          = Column(JSONB,       nullable=True)
    organizer          = Column(JSONB,       nullable=True)
    status             = Column(String(16),  nullable=False, server_default=text("'confirmed'"))
    recurrence_master_id = Column(String(255), nullable=True)
    last_modified_at   = Column(DateTime(timezone=True), nullable=True)
    last_synced_at     = Column(DateTime(timezone=True), nullable=False,
                                server_default=text("CURRENT_TIMESTAMP"))
    created_at         = Column(DateTime(timezone=True), nullable=False,
                                server_default=text("CURRENT_TIMESTAMP"))
    updated_at         = Column(DateTime(timezone=True), nullable=False,
                                server_default=text("CURRENT_TIMESTAMP"))
    raw_payload        = Column(JSONB,       nullable=True)

    user       = relationship("AppUser", backref="calendar_events")
    credential = relationship("UserCredential", backref="calendar_events")

    __table_args__ = (
        CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_calendar_event_provider",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'tentative', 'cancelled')",
            name="ck_user_calendar_event_status",
        ),
        UniqueConstraint(
            "source_credential_id", "source_event_id",
            name="uq_user_calendar_event_source",
        ),
        Index("ix_user_calendar_event_user_start", "user_id", "start_at"),
        Index(
            "ix_user_calendar_event_user_status_active",
            "user_id", "start_at",
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserCalendarEvent {self.start_at} {self.title!r}>"
