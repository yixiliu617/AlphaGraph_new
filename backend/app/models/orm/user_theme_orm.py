from __future__ import annotations
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
import uuid
from backend.app.db.phase2_session import Phase2Base


class UserTheme(Phase2Base):
    """Free-text themes typed by user, tagged to sector (NULL = cross-sector)."""
    __tablename__ = "user_theme"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True),
                        ForeignKey("app_user.id", ondelete="CASCADE"),
                        nullable=False)
    sector_id  = Column(String(64),
                        ForeignKey("gics_sector.id", ondelete="SET NULL"),
                        nullable=True)
    theme_text = Column(Text,    nullable=False)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("ix_user_theme_user_sector", "user_id", "sector_id"),
    )
