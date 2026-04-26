from sqlalchemy import create_engine, event
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


# When the underlying DB is SQLite (dev), enable WAL journaling so concurrent
# readers don't block on a writer. Without WAL, a single in-flight write
# (e.g. a heartbeat upsert) locks the whole DB and any concurrent reader
# stalls. WAL gives reader/writer concurrency at SQLite-level (still single
# writer, but readers run unblocked).
if settings.POSTGRES_URI.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; faster writes
        cur.execute("PRAGMA busy_timeout=5000")   # wait 5s on lock contention
        cur.close()


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


# Ensure non-ORM Taiwan heartbeat table exists on SQLite dev DBs.
# (Postgres deployments should use Alembic; this is dev-convenience only.)
def _ensure_taiwan_heartbeat_table():
    import sqlite3
    from backend.app.services.taiwan.health import ensure_heartbeat_table

    uri = settings.POSTGRES_URI
    if uri.startswith("sqlite:///"):
        db_path = uri.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        try:
            ensure_heartbeat_table(conn)
        finally:
            conn.close()


_ensure_taiwan_heartbeat_table()
