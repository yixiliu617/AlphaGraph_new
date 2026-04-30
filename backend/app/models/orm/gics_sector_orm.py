from __future__ import annotations
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, text
from backend.app.db.phase2_session import Phase2Base


class GicsSector(Phase2Base):
    __tablename__ = "gics_sector"

    id                = Column(String(64),  primary_key=True)
    parent_sector_id  = Column(String(64),
                               ForeignKey("gics_sector.id", ondelete="SET NULL"),
                               nullable=True)
    display_name      = Column(String(128), nullable=False)
    is_industry_group = Column(Boolean,     nullable=False, server_default=text("false"))
    is_synthetic      = Column(Boolean,     nullable=False, server_default=text("false"))
    sort_order        = Column(Integer,     nullable=False, server_default=text("0"))
