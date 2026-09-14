"""Phase 1 application-core integration tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text

from jobs_status_manager.application_core import execution
from jobs_status_manager.application_core.domain import (
    ApplicationStatus,
    StatusUpdateArguments,
    UserId,
)
from jobs_status_manager.application_core.errors import AmbiguousPendingActionError
from jobs_status_manager.application_core.service import (
    execute_status_update,
    propose_status_update,
    recover_pending_actions,
    resolve_confirmation,
    retry_pending_action,
)
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.infrastructure.database.migrations import (
    current_revision,
    upgrade_database,
)
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.clock import FakeClock
    from jobs_status_manager.infrastructure.database.connection import Database


class InjectedStatusEventError(RuntimeError):
    """Failure used to verify transaction rollback."""


def _ids() -> DeterministicIdGenerator:
    """Provide enough IDs for one proposal and one execution."""
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 20)]
    )


def _setup(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> str:
    """Migrate and bootstrap one isolated user."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    identity = bootstrap_identity(database, settings, fake_clock, ids)
    return identity.user_id


def test_proposal_confirmation_execution_is_atomic_and_idempotent(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    """Proposal is inert; confirmation creates one complete business result."""
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    arguments = StatusUpdateArguments(
        company="腾讯", department=None, position="后端开发", status=ApplicationStatus.APPLIED
    )
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="test-1",
        arguments=arguments,
        clock=fake_clock,
        ids=ids,
    )
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0
    result = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    assert result is not None
    execution = execute_status_update(database, action_id=action.id, clock=fake_clock, ids=ids)
    duplicate = execute_status_update(database, action_id=action.id, clock=fake_clock, ids=ids)
    assert execution.changed is True
    assert duplicate.changed is False
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM outbox_events "
                    "WHERE event_type = 'APPLICATION_STATUS_CHANGED'"
                )
            ).scalar_one()
            == 1
        )


def test_noop_and_round_change_have_expected_side_effects(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    """No-op changes are quiet while interview rounds create events."""
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    first = StatusUpdateArguments(
        company="腾讯",
        department=None,
        position="后端开发",
        status=ApplicationStatus.INTERVIEW,
        interview_round=1,
    )
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="one",
        arguments=first,
        clock=fake_clock,
        ids=ids,
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    execute_status_update(database, action_id=action.id, clock=fake_clock, ids=ids)
    no_op = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="two",
        arguments=first,
        clock=fake_clock,
        ids=ids,
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {no_op.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    result = execute_status_update(database, action_id=no_op.id, clock=fake_clock, ids=ids)
    assert result.changed is False
    second = first.model_copy(update={"interview_round": 2})
    round_action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="three",
        arguments=second,
        clock=fake_clock,
        ids=ids,
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {round_action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    assert execute_status_update(
        database, action_id=round_action.id, clock=fake_clock, ids=ids
    ).changed
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 2


def test_rejection_and_multiple_actions_do_not_write(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    """Rejection is inert and code-less multi-action confirmation is unsafe."""
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    base = {
        "company": "腾讯",
        "department": None,
        "position": "后端",
        "status": ApplicationStatus.APPLIED,
    }
    first = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="a",
        arguments=StatusUpdateArguments(**base),
        clock=fake_clock,
        ids=ids,
    )
    second = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="b",
        arguments=StatusUpdateArguments(**base),
        clock=fake_clock,
        ids=ids,
    )
    with pytest.raises(AmbiguousPendingActionError):
        resolve_confirmation(
            database, user_id=UserId(user_id), command_text="确认", clock=fake_clock, ids=ids
        )
    result = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"拒绝 {first.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    assert result is not None
    assert result.message == "pending action rejected"
    assert second.state == "PENDING"
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0


def test_phase_one_migration_and_foreign_keys(database: Database, settings: AppSettings) -> None:
    """The real migration creates all Phase 1 tables and keeps FK enforcement."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    assert (
        current_revision(f"sqlite:///{settings.database_path}")
        == "0009_tool_call_provider_metadata"
    )
    with database.engine.connect() as connection:
        names = set(
            connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()
        )
        assert {"applications", "job_events", "pending_actions", "outbox_events"} <= names
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_recovery_executes_confirmed_action(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    """A confirmed action is resumed from persisted state after a scan."""
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    arguments = StatusUpdateArguments(
        company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
    )
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="recovery",
        arguments=arguments,
        clock=fake_clock,
        ids=ids,
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    results = recover_pending_actions(database, clock=fake_clock, ids=ids)
    assert len(results) == 1
    assert results[0].state.value == "COMPLETED"
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1


def test_stale_executing_action_becomes_failed_without_auto_creating_facts(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="stale",
        arguments=StatusUpdateArguments(
            company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
        ),
        clock=fake_clock,
        ids=ids,
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pending_actions SET state='EXECUTING', execution_started_at=:started "
                "WHERE id=:id"
            ),
            {"started": (fake_clock.now() - timedelta(minutes=6)).isoformat(), "id": action.id},
        )

    results = recover_pending_actions(database, clock=fake_clock, ids=ids)

    assert results == []
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT state FROM pending_actions")).scalar_one() == "FAILED"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0


def test_manual_retry_only_requeues_existing_failed_action(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="manual-retry",
        arguments=StatusUpdateArguments(
            company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
        ),
        clock=fake_clock,
        ids=ids,
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pending_actions SET state='FAILED', last_error='provider failed' "
                "WHERE id=:id"
            ),
            {"id": action.id},
        )

    retried = retry_pending_action(database, action_id=action.id, clock=fake_clock)

    assert retried.state.value == "CONFIRMED"
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0


def test_terminal_failed_action_is_not_auto_retried(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="terminal",
        arguments=StatusUpdateArguments(
            company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
        ),
        clock=fake_clock,
        ids=ids,
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pending_actions SET state='FAILED', attempt_count=3, next_retry_at=:now "
                "WHERE id=:id"
            ),
            {"now": fake_clock.now().isoformat(), "id": action.id},
        )

    assert recover_pending_actions(database, clock=fake_clock, ids=ids) == []


def test_status_execution_rolls_back_all_business_writes_on_failure(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while building the status event rolls back the whole write transaction."""
    ids = _ids()
    user_id = _setup(database, settings, fake_clock, ids)
    action = propose_status_update(
        database,
        user_id=UserId(user_id),
        source_id="rollback",
        arguments=StatusUpdateArguments(
            company="腾讯", department=None, position="后端", status=ApplicationStatus.APPLIED
        ),
        clock=fake_clock,
        ids=ids,
    )
    resolved = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    assert resolved is not None

    def fail_status_event(**kwargs: object) -> object:
        raise InjectedStatusEventError

    monkeypatch.setattr(execution, "_status_event", fail_status_event)
    with pytest.raises(InjectedStatusEventError):
        execute_status_update(database, action_id=action.id, clock=fake_clock, ids=ids)

    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM outbox_events "
                    "WHERE event_type = 'APPLICATION_STATUS_CHANGED'"
                )
            ).scalar_one()
            == 0
        )
        failure = connection.execute(
            text("SELECT state, attempt_count, last_error FROM pending_actions WHERE id = :id"),
            {"id": action.id},
        ).one()
        assert failure.state == "FAILED"
        assert failure.attempt_count == 1
        assert failure.last_error == safe_external_error(InjectedStatusEventError())
