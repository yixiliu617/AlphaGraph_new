from sqlalchemy import Column, String, JSON, DateTime, ARRAY, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()

class FragmentORM(Base):
    """
    PostgreSQL SQLAlchemy Model for DataFragment.
    Enforces strict public/private isolation at the database layer.

    Deduplication:
      content_fingerprint is a SHA-256 hex digest of
      "{tenant_id}:{source_document_id}:{exact_location}".
      It is stored as an indexed column and checked before every insert
      so the same page/chunk from the same document is never stored twice.
    """
    __tablename__ = "data_fragments"

    fragment_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    tenant_tier = Column(String, nullable=False)

    lineage = Column(JSON, default=[])
    source_type = Column(String, nullable=False)
    source = Column(String, nullable=False)
    exact_location = Column(String, nullable=False)

    reason_for_extraction = Column(String, nullable=False)

    # The JSON payload (raw_text + extracted_metrics)
    content = Column(JSON, nullable=False)

    # Dedup fingerprint — nullable so old rows without it still load cleanly.
    # Unique constraint is enforced at the application layer (adapter checks
    # before insert) rather than DB layer so it works on both SQLite and Postgres
    # without a migration tool.
    content_fingerprint = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)

class RecipeORM(Base):
    """
    PostgreSQL SQLAlchemy Model for ExtractionRecipe.
    """
    __tablename__ = "extraction_recipes"

    recipe_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    
    target_sectors = Column(JSON, default=[])
    version = Column(String, nullable=False)
    ingestor_type = Column(String, nullable=False)
    
    llm_prompt_template = Column(String, nullable=False)
    expected_schema = Column(JSON, nullable=False)
    
    is_public = Column(String, default="false")
    created_at = Column(DateTime, default=datetime.utcnow)
