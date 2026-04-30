from __future__ import annotations
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey,
                        Integer, String, text)
from sqlalchemy.dialects.postgresql import UUID
from backend.app.db.phase2_session import Phase2Base


class UserProfile(Phase2Base):
    """Wizard answers + onboarding state. One-to-one with app_user."""
    __tablename__ = "user_profile"

    user_id              = Column(UUID(as_uuid=True),
                                  ForeignKey("app_user.id", ondelete="CASCADE",
                                             name="fk_user_profile_user"),
                                  primary_key=True)
    role                 = Column(String(32),  nullable=True)
    role_other           = Column(String(255), nullable=True)
    firm_strategy        = Column(String(32),  nullable=True)
    firm_strategy_other  = Column(String(255), nullable=True)
    firm_name            = Column(String(255), nullable=True)
    is_generalist        = Column(Boolean,     nullable=False,
                                  server_default=text("false"))
    wizard_current_step  = Column(Integer,     nullable=False,
                                  server_default=text("1"))
    wizard_completed_at  = Column(DateTime(timezone=True), nullable=True)
    wizard_skipped_at    = Column(DateTime(timezone=True), nullable=True)
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  server_default=text("CURRENT_TIMESTAMP"))
    updated_at           = Column(DateTime(timezone=True), nullable=False,
                                  server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("wizard_current_step BETWEEN 1 AND 6",
                        name="ck_user_profile_step"),
    )
