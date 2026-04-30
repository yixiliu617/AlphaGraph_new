from __future__ import annotations
from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Index,
                        String, Text, text)
from sqlalchemy.dialects.postgresql import UUID
import uuid
from backend.app.db.phase2_session import Phase2Base


class WaitlistEntry(Phase2Base):
    """Invite-only waitlist queue. Sharon manually approves; founding-member
    referrals auto-approve. The `email` column is CITEXT so case differences
    don't create duplicate rows (b@x.com == B@X.com)."""
    __tablename__ = "waitlist_entry"

    id                      = Column(UUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"),
                                     default=uuid.uuid4)
    # CITEXT type isn't built into SA dialects; use String at the ORM layer
    # and rely on the DB CITEXT column type (set in the migration).
    email                   = Column(String(320), nullable=False, unique=True)
    full_name               = Column(String(255), nullable=True)
    self_reported_role      = Column(String(64),  nullable=True)
    self_reported_firm      = Column(String(255), nullable=True)
    note                    = Column(Text,        nullable=True)
    referrer                = Column(String(255), nullable=True)
    referred_by_user_id     = Column(UUID(as_uuid=True),
                                     ForeignKey("app_user.id", ondelete="SET NULL"),
                                     nullable=True)
    status                  = Column(String(32),  nullable=False,
                                     server_default=text("'pending'"))
    requested_at            = Column(DateTime(timezone=True), nullable=False,
                                     server_default=text("CURRENT_TIMESTAMP"))
    approved_at             = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id     = Column(UUID(as_uuid=True),
                                     ForeignKey("app_user.id", ondelete="SET NULL"),
                                     nullable=True)
    rejected_reason         = Column(Text, nullable=True)
    invite_email_sent_at    = Column(DateTime(timezone=True), nullable=True)
    invite_email_clicked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'self_serve_attempt')",
            name="ck_waitlist_status",
        ),
        Index("ix_waitlist_status_requested",
              "status", "requested_at"),
    )
