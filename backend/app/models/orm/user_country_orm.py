from __future__ import annotations
from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from backend.app.db.phase2_session import Phase2Base


class UserCountry(Phase2Base):
    __tablename__ = "user_country"

    user_id      = Column(UUID(as_uuid=True),
                          ForeignKey("app_user.id", ondelete="CASCADE"),
                          primary_key=True)
    country_code = Column(String(8), primary_key=True)
    custom_label = Column(String(255), nullable=True)
    selected_at  = Column(DateTime(timezone=True), nullable=False,
                          server_default=text("CURRENT_TIMESTAMP"))
