"""Programmatic Alembic migration runner."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from jobs_status_manager.infrastructure.paths import prepare_data_paths


def migration_config(project_root: Path, database_url: str) -> Config:
    """Build an Alembic configuration independent of the current directory."""
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_database(project_root: Path, database_url: str) -> None:
    """Apply all repository migrations."""
    if database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
        prepare_data_paths(database_path.parent, database_path)
    command.upgrade(migration_config(project_root, database_url), "head")


def current_revision(database_url: str) -> str | None:
    """Return the current schema revision, if the database has one."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            if not connection.dialect.has_table(connection, "schema_migrations"):
                return None
            revision = connection.execute(
                text("SELECT version_num FROM schema_migrations")
            ).scalar_one_or_none()
            return str(revision) if revision is not None else None
    finally:
        engine.dispose()
