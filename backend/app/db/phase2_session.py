"""
SQLAlchemy session for the Phase 2 tables (app_user, oauth_session,
user_alert) on Postgres.

Kept SEPARATE from `backend/app/db/session.py` (which holds the legacy
Fragment / Universe / Insight / Note tables on whatever POSTGRES_URI
points at — by default SQLite). This separation lets the existing app
keep running on SQLite while the new auth surface lives in Postgres,
without forcing a same-day migration of every legacy table.

When we eventually consolidate everything to Postgres:
  1. Re-target POSTGRES_URI at the same Postgres instance.
  2. Add migrations for the legacy tables (one per concern).
  3. This file collapses into the main `session.py`.

Until then, every Phase 2 ORM extends `Phase2Base` and reads/writes via
`Phase2SessionLocal`.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.app.core.config import settings


Phase2Base = declarative_base()

# pool_pre_ping=True so connections that have been killed by Postgres
# (e.g. a server-side timeout, a docker compose restart) are detected and
# replaced lazily. Cheap on the request path; saves a class of "OperationalError:
# server closed the connection unexpectedly" mid-flight.
phase2_engine = create_engine(
    settings.auth_db_uri,
    pool_pre_ping=True,
    future=True,
)

Phase2SessionLocal = sessionmaker(
    bind=phase2_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def get_phase2_session():
    """FastAPI dependency. Yields a SQLAlchemy session bound to the auth DB.
    Always closes the session at the end of the request."""
    db = Phase2SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Side-effect imports — register every Phase 2 ORM in Phase2Base.metadata
# at module-load time so SQLAlchemy can resolve relationship() string
# references when mappers configure on first query. Without this, a script
# that imports only one ORM (e.g. credential_orm) hits InvalidRequestError
# when the AppUser ↔ UserCredential relationship tries to resolve.
# Done at the bottom — Phase2Base + the engine are fully defined above.
from backend.app.models.orm import user_orm             # noqa: F401, E402
from backend.app.models.orm import alert_orm            # noqa: F401, E402
from backend.app.models.orm import credential_orm       # noqa: F401, E402
from backend.app.models.orm import calendar_event_orm   # noqa: F401, E402
from backend.app.models.orm import note_synced_orm      # noqa: F401, E402
from backend.app.models.orm import universe_v2_orm      # noqa: F401, E402
from backend.app.models.orm import waitlist_orm        # noqa: F401, E402
from backend.app.models.orm import user_profile_orm    # noqa: F401, E402
from backend.app.models.orm import gics_sector_orm     # noqa: F401, E402
from backend.app.models.orm import user_sector_orm     # noqa: F401, E402
from backend.app.models.orm import user_country_orm    # noqa: F401, E402
from backend.app.models.orm import user_theme_orm      # noqa: F401, E402
