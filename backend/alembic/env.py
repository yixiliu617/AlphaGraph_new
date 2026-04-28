"""
Alembic environment for AlphaGraph.

Reads the database URL from `backend.app.core.config.settings.POSTGRES_URI`.
Switching databases is therefore a .env / settings concern — not a config
file edit. In practice:

    POSTGRES_URI=postgresql+psycopg2://alphagraph:alphagraph_dev@localhost:5432/alphagraph

In dev. RDS in production.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `backend.app...` importable when alembic runs from anywhere.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.core.config import settings  # noqa: E402

# Phase 2 ORM models live under a SEPARATE Base/engine (Phase2Base) so
# the legacy Fragment/etc. tables on POSTGRES_URI (sqlite by default)
# don't interfere with auth tables on AUTH_DATABASE_URI (postgres).
from backend.app.db.phase2_session import Phase2Base  # noqa: E402
import backend.app.models.orm.user_orm        # noqa: F401, E402
import backend.app.models.orm.alert_orm       # noqa: F401, E402
import backend.app.models.orm.credential_orm  # noqa: F401, E402

# Legacy ORMs — kept here so a future migration can pick them up if/when
# we consolidate everything onto Postgres. They are NOT in target_metadata
# below, so autogenerate ignores them right now.
import backend.app.models.orm.fragment_orm   # noqa: F401, E402
import backend.app.models.orm.universe_orm   # noqa: F401, E402
import backend.app.models.orm.insight_orm    # noqa: F401, E402
import backend.app.models.orm.note_orm       # noqa: F401, E402

config = context.config

# Inject the URL from project settings — auth_db_uri (= AUTH_DATABASE_URI
# or POSTGRES_URI fallback) is what hosts the Phase 2 schema.
config.set_main_option("sqlalchemy.url", settings.auth_db_uri)

# Logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Phase2Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL for offline review without connecting to a database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
