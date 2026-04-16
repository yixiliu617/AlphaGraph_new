"""
Integration-tier fixtures.

Provides a fresh in-memory SQLite session per test — no Postgres needed.
All ORM models are registered and tables are created/dropped around each test,
giving each test a completely clean slate.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Import Base and register every ORM model before calling create_all.
from backend.app.models.orm.fragment_orm import Base
import backend.app.models.orm.universe_orm  # side-effect: registers ORM classes
import backend.app.models.orm.insight_orm   # side-effect: registers insight ORM classes

from backend.app.adapters.db.postgres_adapter import PostgresAdapter


@pytest.fixture
def sqlite_session():
    """
    Yields a SQLAlchemy Session backed by an in-memory SQLite database.
    Tables are created before the test and dropped afterwards — fully isolated.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sqlite_db_repo(sqlite_session) -> PostgresAdapter:
    """
    A real PostgresAdapter wired to the in-memory SQLite session.
    Uses the same code path as production — only the engine differs.
    """
    return PostgresAdapter(sqlite_session)
