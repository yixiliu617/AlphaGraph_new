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

# Import every model module so SQLAlchemy's Base.metadata is fully populated
# before autogenerate compares against the live schema. Add new model imports
# here whenever a new ORM table is introduced.
from backend.app.models.orm.fragment_orm import Base  # noqa: E402
import backend.app.models.orm.universe_orm  # noqa: F401, E402
import backend.app.models.orm.insight_orm   # noqa: F401, E402
import backend.app.models.orm.note_orm      # noqa: F401, E402
# New Phase 2 models will be imported here as they're added:
# import backend.app.models.orm.user_orm    # noqa: F401, E402
# import backend.app.models.orm.alert_orm   # noqa: F401, E402

config = context.config

# Inject the URL from project settings.
config.set_main_option("sqlalchemy.url", settings.POSTGRES_URI)

# Logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


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
