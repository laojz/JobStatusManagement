"""Short-lived synchronous SQLite connections."""

import sqlite3
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, create_engine, event, text

from jobs_status_manager.infrastructure.paths import prepare_data_paths

BUSY_TIMEOUT_MS: Final = 5000


class Database:
    """Database engine factory with explicit SQLite safety pragmas."""

    path: Path
    engine: Engine

    def __init__(self, path: Path) -> None:
        """Create an engine for a file-backed SQLite database."""
        prepare_data_paths(path.parent, path)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
            future=True,
        )
        event.listen(self.engine, "connect", self._configure_connection)

    @staticmethod
    def _configure_connection(dbapi_connection: sqlite3.Connection, _: object) -> None:
        """Configure every SQLite connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.close()

    def pragmas(self) -> dict[str, str]:
        """Read the configured SQLite pragmas for diagnostics."""
        with self.engine.connect() as connection:
            return {
                "foreign_keys": str(connection.execute(text("PRAGMA foreign_keys")).scalar_one()),
                "journal_mode": str(connection.execute(text("PRAGMA journal_mode")).scalar_one()),
                "busy_timeout": str(connection.execute(text("PRAGMA busy_timeout")).scalar_one()),
            }

    def dispose(self) -> None:
        """Close pooled connections."""
        self.engine.dispose()
