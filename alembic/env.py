from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import the ORM Base (with all models) for autogenerate support
from app.models.database import Base  # noqa: F401
from app.core.config import settings

# this is the Alembic Config object
config = context.config

# Override sqlalchemy.url with the DATABASE_URL from environment / settings
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata from our ORM Base
target_metadata = Base.metadata


def _build_connect_args(database_url: str) -> dict:
    """Return safe connection args for migrations in managed Postgres providers."""
    connect_args = {"connect_timeout": 60}

    is_supabase = "supabase.co" in database_url or "pooler.supabase.com" in database_url
    has_ssl_mode = "sslmode=" in database_url
    if is_supabase and not has_ssl_mode:
        connect_args["sslmode"] = "require"

    return connect_args


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    db_url = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_build_connect_args(db_url),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
