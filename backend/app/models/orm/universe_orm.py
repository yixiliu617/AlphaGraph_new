from sqlalchemy import Column, String, JSON, Boolean, ForeignKey
from backend.app.models.orm.fragment_orm import Base
import uuid

class PublicCompanyORM(Base):
    __tablename__ = "public_universe"
    
    ticker = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    gics_sector = Column(String, index=True)
    gics_subsector = Column(String, index=True)
    gics_subindustry = Column(String, index=True)
    company_metadata = Column(JSON, default={})

class UserCompanyORM(Base):
    __tablename__ = "user_universe"
    
    company_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    ticker = Column(String, nullable=False)
    user_category_1 = Column(String, index=True)
    user_category_2 = Column(String, index=True)
    is_active = Column(Boolean, default=True)
