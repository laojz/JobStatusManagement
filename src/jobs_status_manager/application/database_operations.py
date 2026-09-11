"""SQLite backup and validation operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from alembic.config import Config
from alembic.script import ScriptDirectory

from jobs_status_manager.application.operation_contracts import (
    BackupResult,
    DatabaseMaintenanceError,
    IntegrityResult,
)
from jobs_status_manager.infrastructure.database.migrations import current_revision
from jobs_status_manager.infrastructure.paths import ensure_private_directory
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.database.connection import Database

CORE_TABLES = (
    "applications",
    "job_events",
    "mails",
    "pending_actions",
    "notifications",
    "knowledge_documents",
    "knowledge_chunks",
)


def _head_revision(project_root: Path) -> str:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        message = "migration head is unavailable"
        raise DatabaseMaintenanceError(message)
    return head


def _queryable_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    names = tuple(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    missing = tuple(table for table in CORE_TABLES if table not in names)
    if missing:
        return missing
    queries = {
        "applications": "SELECT 1 FROM applications LIMIT 1",
        "job_events": "SELECT 1 FROM job_events LIMIT 1",
        "mails": "SELECT 1 FROM mails LIMIT 1",
        "pending_actions": "SELECT 1 FROM pending_actions LIMIT 1",
        "notifications": "SELECT 1 FROM notifications LIMIT 1",
        "knowledge_documents": "SELECT 1 FROM knowledge_documents LIMIT 1",
        "knowledge_chunks": "SELECT 1 FROM knowledge_chunks LIMIT 1",
    }
    for table in CORE_TABLES:
        connection.execute(queries[table])
    return ()


def check_integrity(path: Path, project_root: Path) -> IntegrityResult:
    """Validate SQLite integrity, foreign keys, schema head, and core tables."""
    connection = sqlite3.connect(path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = tuple(str(row) for row in connection.execute("PRAGMA foreign_key_check"))
        try:
            missing_tables = _queryable_tables(connection)
        except sqlite3.DatabaseError as error:
            missing_tables = (safe_external_error(error),)
    finally:
        connection.close()
    revision = current_revision(f"sqlite:///{path}")
    expected = _head_revision(project_root)
    ok = integrity == "ok" and not foreign_keys and not missing_tables and revision == expected
    return IntegrityResult(ok, integrity, foreign_keys, missing_tables, revision)


def backup_database(database: Database, destination: Path, project_root: Path) -> BackupResult:
    """Create a native SQLite backup without overwriting user files."""
    source = database.path.resolve()
    target = destination.resolve()
    if source == target:
        message = "destination must not be the live database"
        raise DatabaseMaintenanceError(message)
    if target.exists():
        message = "destination already exists"
        raise DatabaseMaintenanceError(message)
    ensure_private_directory(target.parent)
    source_connection = sqlite3.connect(source)
    target_connection: sqlite3.Connection | None = None
    try:
        target_connection = sqlite3.connect(target)
        source_connection.backup(target_connection)
    except sqlite3.DatabaseError as error:
        if target.exists():
            target.unlink()
        message = "backup failed"
        raise DatabaseMaintenanceError(message) from error
    finally:
        source_connection.close()
        if target_connection is not None:
            target_connection.close()
    target.chmod(0o600)
    integrity = check_integrity(target, project_root)
    if not integrity.ok:
        target.unlink(missing_ok=True)
        message = "backup validation failed"
        raise DatabaseMaintenanceError(message)
    return BackupResult(target, integrity)


def restore_check(source: Path, project_root: Path) -> IntegrityResult:
    """Validate a backup in a fresh temporary SQLite database."""
    with TemporaryDirectory(prefix="jobs-restore-check-") as directory:
        temporary = Path(directory) / "restored.db"
        source_connection = sqlite3.connect(source)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            source_connection.close()
            target_connection.close()
        return check_integrity(temporary, project_root)
