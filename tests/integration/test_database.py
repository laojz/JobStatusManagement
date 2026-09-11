"""SQLite and migration integration tests."""

from pathlib import Path

from alembic import command
from sqlalchemy import inspect, text

from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import (
    current_revision,
    migration_config,
    upgrade_database,
)
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator


def fail_transaction() -> None:
    """Raise the deliberate exception used by rollback verification."""
    message = "rollback"
    raise RuntimeError(message)


def test_migrations_pragmas_and_identity_bootstrap(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> None:
    """Migrations are versioned and bootstrap is idempotent."""
    root = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{settings.database_path}"
    upgrade_database(root, url)
    upgrade_database(root, url)
    assert current_revision(url) == "0007_phase6_reliability"
    assert database.pragmas() == {
        "foreign_keys": "1",
        "journal_mode": "wal",
        "busy_timeout": "5000",
    }
    assert set(inspect(database.engine).get_table_names()) == {
        "applications",
        "job_events",
        "mail_accounts",
        "mails",
        "job_mail_analyses",
        "outbox_events",
        "pending_actions",
        "processed_events",
        "notifications",
        "notification_attempts",
        "schema_migrations",
        "sessions",
        "conversation_messages",
        "agent_runs",
        "tool_calls",
        "tool_results",
        "qq_inbound_identities",
        "user_files",
        "knowledge_documents",
        "knowledge_chunks",
        "users",
    }
    first = bootstrap_identity(database, settings, fake_clock, ids)
    second = bootstrap_identity(database, settings, fake_clock, ids)
    assert first.user_id == second.user_id
    assert first.mail_account_id == second.mail_account_id
    assert first.created is True
    assert second.created is False
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM mail_accounts")).scalar_one() == 1


def test_transaction_rolls_back(database: Database, settings: AppSettings) -> None:
    """An exception removes all writes from the failed transaction."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    try:
        with transaction(database) as session:
            session.execute(
                text(
                    "INSERT INTO users (id, external_key, display_name, created_at, updated_at) "
                    "VALUES ('id', 'key', 'name', '2026-01-01', '2026-01-01')"
                )
            )
            fail_transaction()
    except RuntimeError:
        pass
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 0


def test_phase_six_migration_round_trip_preserves_run_timing(
    database: Database, settings: AppSettings
) -> None:
    root = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{settings.database_path}"
    upgrade_database(root, url)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, external_key, display_name, created_at, updated_at) "
                "VALUES ('user', 'key', 'name', :now, :now)"
            ),
            {"now": "2026-01-01T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, created_at, "
                "last_active_at, updated_at) VALUES "
                "('session', 'user', 'MAIN', '', :now, :now, :now)"
            ),
            {"now": "2026-01-01T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages (id, session_id, role, content, created_at) "
                "VALUES ('message', 'session', 'user', 'hello', :now)"
            ),
            {"now": "2026-01-01T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": "2026-01-01T00:00:00+00:00"},
        )
    command.downgrade(migration_config(root, url), "0006_phase5_rag")
    assert current_revision(url) == "0006_phase5_rag"
    command.upgrade(migration_config(root, url), "head")
    assert current_revision(url) == "0007_phase6_reliability"
    with database.engine.connect() as connection:
        run = connection.execute(
            text("SELECT started_at, attempt_count, next_retry_at FROM agent_runs WHERE id='run'")
        ).one()
        assert run.started_at in {
            "2026-01-01 00:00:00.000000",
            "2026-01-01T00:00:00+00:00",
        }
        assert run.attempt_count == 0
        assert run.next_retry_at is None
        indexes = {
            row[1]: tuple(
                index_row[2]
                for index_row in connection.execute(text(f"PRAGMA index_info('{row[1]}')"))
            )
            for row in connection.execute(text("PRAGMA index_list('agent_runs')"))
        }
        assert indexes["ix_agent_runs_stale_scan"] == ("state", "started_at")
        assert indexes["ix_agent_runs_retry_scan"] == ("state", "next_retry_at")
        pending_indexes = {
            row[1]: tuple(
                index_row[2]
                for index_row in connection.execute(text(f"PRAGMA index_info('{row[1]}')"))
            )
            for row in connection.execute(text("PRAGMA index_list('pending_actions')"))
        }
        assert pending_indexes["ix_pending_actions_retry_scan"] == (
            "action_type",
            "state",
            "next_retry_at",
        )
        assert pending_indexes["ix_pending_actions_stale_scan"] == (
            "action_type",
            "state",
            "execution_started_at",
        )
