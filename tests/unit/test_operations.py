from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from jobs_status_manager.application.operations import (
    DatabaseMaintenanceError,
    IntegrityResult,
    TaskKind,
    backup_database,
    check_integrity,
    list_tasks,
    restore_check,
    retry_task,
)
from jobs_status_manager.infrastructure.database.migrations import upgrade_database

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.clock import FakeClock
    from jobs_status_manager.infrastructure.database.connection import Database


def test_failed_task_listing_redacts_payload_and_retry_only_changes_selected_row(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    now = fake_clock.now()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, external_key, display_name, created_at, updated_at) "
                "VALUES ('user', 'key', 'name', :now, :now)"
            ),
            {"now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO outbox_events (id, event_id, event_type, event_version, "
                "aggregate_type, aggregate_id, producer, payload, occurred_at, status, "
                "attempt_count, last_error, created_at) VALUES "
                "('event', 'event', 'MAIL_RECEIVED', 1, 'Mail', 'mail-id', 'test', "
                ":payload, :now, 'FAILED', 3, :error, :now)"
            ),
            {
                "payload": '{"content":"private body"}',
                "error": "provider failed",
                "now": now.isoformat(),
            },
        )
    tasks = list_tasks(database, include_stale=True)
    assert tasks[0].kind is TaskKind.OUTBOX
    assert tasks[0].task_id == "event"
    assert tasks[0].related_id == "mail-id"
    assert tasks[0].last_error == "provider failed"
    assert "private body" not in repr(tasks[0])
    with database.engine.connect() as connection:
        before = len(list(connection.execute(text("SELECT id FROM outbox_events"))))
    result = retry_task(database, TaskKind.OUTBOX, "event", fake_clock)
    assert result.task_id == "event"
    with database.engine.connect() as connection:
        after = len(list(connection.execute(text("SELECT id FROM outbox_events"))))
    assert before == after == 1


def test_backup_integrity_and_restore_check_use_independent_destination(
    database: Database, settings: AppSettings, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    destination = tmp_path / "backup" / "jobs.db"
    backup = backup_database(database, destination, root)
    assert backup.destination == destination
    assert check_integrity(destination, root).ok
    assert restore_check(destination, root).ok


def test_backup_rejects_live_database_destination(
    database: Database, settings: AppSettings, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    with pytest.raises(DatabaseMaintenanceError, match="live database"):
        backup_database(database, settings.database_path, root)


def test_backup_preserves_existing_destination(
    database: Database, settings: AppSettings, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"operator file")
    with pytest.raises(DatabaseMaintenanceError, match="already exists"):
        backup_database(database, destination, root)
    assert destination.read_bytes() == b"operator file"


def test_integrity_rejects_foreign_key_inconsistency(
    database: Database, settings: AppSettings
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO applications "
                "(id, user_id, company, position, company_key, department_key, position_key, "
                "current_status, created_at, updated_at) VALUES "
                "('bad', 'missing', 'c', 'p', 'c', '', 'p', 'APPLIED', :now, :now)"
            ),
            {"now": "2026-01-01T00:00:00+00:00"},
        )
    result = check_integrity(settings.database_path, root)
    assert isinstance(result, IntegrityResult)
    assert not result.ok
    assert result.foreign_keys
