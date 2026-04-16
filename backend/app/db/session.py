from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.core.config import settings

# Import Base to allow table creation
# Note: We import it here to avoid circular dependencies in some setups,
# but ensure all models are loaded before calling create_all
from backend.app.models.orm.fragment_orm import Base
import backend.app.models.orm.universe_orm  # Ensure universe models are registered
import backend.app.models.orm.insight_orm   # Ensure insight models are registered
import backend.app.models.orm.note_orm      # Ensure meeting_notes table is registered

engine = create_engine(settings.POSTGRES_URI)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """
    Creates all tables defined in the ORM.
    """
    Base.metadata.create_all(bind=engine)

def get_db_session():
    """
    Dependency generator for database sessions.
    Ensures sessions are closed after the request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
