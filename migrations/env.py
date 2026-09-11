"""Alembic environment."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from jobs_status_manager import mail_models as _mail_models  # noqa: F401
from jobs_status_manager import notification_models as _notification_models  # noqa: F401
from jobs_status_manager.agent import models as _agent_models  # noqa: F401
from jobs_status_manager.application_core import models as _application_core_models  # noqa: F401
from jobs_status_manager.identity.models import Base
from jobs_status_manager.knowledge import models as _knowledge_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table="schema_migrations",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="schema_migrations",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
