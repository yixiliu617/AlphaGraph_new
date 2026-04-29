"""
ORM model for `user_note` — synced notes (OneNote, etc.).

Note: filename is `note_synced_orm.py` (not `note_orm.py`) because there's
already a legacy `note_orm.py` for the `meeting_notes` table — different
table, different concern. Naming is "synced" to mark this as the Phase
2 sync surface (third-party note sources).
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


class UserNote(Phase2Base):
    __tablename__ = "user_note"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE",
                   name="fk_user_note_user"),
        nullable=False, index=True,
    )
    source_credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("user_credential.id", ondelete="CASCADE",
                   name="fk_user_note_credential"),
        nullable=False,
    )
    source_note_id = Column(String(255), nullable=False)
    provider       = Column(String(32),  nullable=False)
    service        = Column(String(64),  nullable=False)

    title             = Column(Text,         nullable=True)
    notebook_id       = Column(String(255),  nullable=True)
    notebook_name     = Column(String(512),  nullable=True)
    section_id        = Column(String(255),  nullable=True)
    section_name      = Column(String(512),  nullable=True)
    page_link         = Column(String(2048), nullable=True)
    content_html      = Column(Text,         nullable=True)
    content_text      = Column(Text,         nullable=True)
    content_truncated = Column(Boolean,      nullable=False, server_default=text("false"))

    created_at_remote        = Column(DateTime(timezone=True), nullable=True)
    last_modified_at_remote  = Column(DateTime(timezone=True), nullable=True)
    last_synced_at           = Column(DateTime(timezone=True), nullable=False,
                                      server_default=text("CURRENT_TIMESTAMP"))
    created_at               = Column(DateTime(timezone=True), nullable=False,
                                      server_default=text("CURRENT_TIMESTAMP"))
    updated_at               = Column(DateTime(timezone=True), nullable=False,
                                      server_default=text("CURRENT_TIMESTAMP"))
    raw_payload              = Column(JSONB,                   nullable=True)

    user       = relationship("AppUser", backref="notes")
    credential = relationship("UserCredential", backref="notes")

    __table_args__ = (
        CheckConstraint(
            "provider IN ('google', 'microsoft')",
            name="ck_user_note_provider",
        ),
        UniqueConstraint(
            "source_credential_id", "source_note_id",
            name="uq_user_note_source",
        ),
        Index("ix_user_note_user_modified",
              "user_id", "last_modified_at_remote"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserNote {self.notebook_name}/{self.title!r}>"
