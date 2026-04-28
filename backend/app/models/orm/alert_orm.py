"""
ORM model for the user_alert table.

`condition_dsl` is the raw rule string a user types
(e.g. `NVDA close > 200` or `AAPL RSI(14) < 30`). `condition_parsed` is
the JSONB form the alert evaluator runs against. We keep both side by
side so the original text shows in the UI exactly as the user wrote it,
and the parsed form is what the backend evaluates on each tick. A
mismatch on re-parse is an explicit migration signal (DSL change).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.app.db.phase2_session import Phase2Base


class UserAlert(Phase2Base):
    __tablename__ = "user_alert"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE", name="fk_user_alert_user"),
        nullable=False, index=True,
    )
    ticker            = Column(String(32), nullable=False)
    condition_dsl     = Column(Text,       nullable=False)
    condition_parsed  = Column(JSONB,      nullable=True)
    status            = Column(String(16), nullable=False, server_default=text("'active'"), index=True)
    created_at        = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)
    triggered_at      = Column(DateTime(timezone=True), nullable=True)
    trigger_value     = Column(JSONB,      nullable=True)
    note              = Column(Text,       nullable=True)

    user = relationship("AppUser", back_populates="alerts")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'triggered', 'expired', 'archived')",
            name="ck_user_alert_status",
        ),
        # Hot-path partial index — the evaluator scans ticker filtered to active.
        Index(
            "ix_user_alert_ticker_active", "ticker", "status",
            postgresql_where=text("status = 'active'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserAlert {self.ticker} [{self.status}] {self.condition_dsl[:40]!r}>"
