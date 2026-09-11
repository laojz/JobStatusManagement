"""Atomic execution and synchronous recovery for confirmed status actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import SQLAlchemyError

from jobs_status_manager.application_core.domain import (
    ApplicationStatus,
    EventType,
    OutboxStatus,
    PendingActionState,
    StatusUpdateArguments,
    status_changed,
)
from jobs_status_manager.application_core.errors import (
    ApplicationCoreError,
    InvalidPendingActionTransitionError,
    PendingActionNotFoundError,
    ResolvedArgumentsTamperedError,
)
from jobs_status_manager.application_core.models import (
    Application,
    JobEvent,
    OutboxEvent,
    PendingAction,
)
from jobs_status_manager.application_core.repositories import (
    find_application,
    find_job_event,
    find_pending_action,
)
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

MAX_PENDING_ACTION_ATTEMPTS: Final = 3
RETRY_DELAY: Final = timedelta(minutes=5)
MAX_ERROR_CHARS: Final = 2000

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Observable outcome of execution."""

    action_id: str
    state: PendingActionState
    message: str
    application_id: str | None = None
    changed: bool = False


def _transition(action: PendingAction, target: PendingActionState) -> None:
    """Apply one legal PendingAction transition."""
    current = PendingActionState(action.state)
    allowed = {
        PendingActionState.PENDING: frozenset(
            {PendingActionState.CONFIRMED, PendingActionState.REJECTED, PendingActionState.EXPIRED}
        ),
        PendingActionState.CONFIRMED: frozenset({PendingActionState.EXECUTING}),
        PendingActionState.EXECUTING: frozenset(
            {PendingActionState.COMPLETED, PendingActionState.FAILED}
        ),
        PendingActionState.FAILED: frozenset({PendingActionState.EXECUTING}),
        PendingActionState.COMPLETED: frozenset(),
        PendingActionState.REJECTED: frozenset(),
        PendingActionState.EXPIRED: frozenset(),
    }
    if target not in allowed[current]:
        raise InvalidPendingActionTransitionError(current.value, target.value)
    action.state = target.value


def _status_event(  # noqa: PLR0913
    *,
    event_id: str,
    application: Application,
    action: PendingAction,
    previous_status: str | None,
    previous_round: int | None,
    now: datetime,
) -> OutboxEvent:
    """Create the application status-change outbox row."""
    return OutboxEvent(
        id=event_id,
        event_id=event_id,
        event_type=EventType.APPLICATION_STATUS_CHANGED.value,
        event_version=1,
        aggregate_type="Application",
        aggregate_id=application.id,
        producer="application_core",
        payload={
            "pending_action_id": action.id,
            "previous_status": previous_status,
            "current_status": application.current_status,
            "previous_interview_round": previous_round,
            "current_interview_round": application.current_interview_round,
        },
        causation_id=action.id,
        occurred_at=now,
        status=OutboxStatus.PENDING.value,
        created_at=now,
        attempt_count=0,
    )


def _arguments(action: PendingAction) -> StatusUpdateArguments:
    """Parse only immutable persisted arguments."""
    try:
        return StatusUpdateArguments.model_validate(action.resolved_arguments)
    except (TypeError, ValueError) as error:
        raise ResolvedArgumentsTamperedError(action.id) from error


def _claim_status_update(
    database: Database, *, action_id: str, clock: Clock
) -> ActionResult | None:
    with transaction(database) as session:
        action = find_pending_action(session, action_id)
        if action is None:
            raise PendingActionNotFoundError(action_id)
        if action.state == PendingActionState.COMPLETED.value:
            event = find_job_event(session, action.id)
            return ActionResult(
                action.id,
                PendingActionState.COMPLETED,
                "already completed",
                event.application_id if event else None,
            )
        if action.state in {
            PendingActionState.FAILED.value,
            PendingActionState.CONFIRMED.value,
        }:
            _transition(action, PendingActionState.EXECUTING)
        else:
            return ActionResult(
                action.id, PendingActionState(action.state), "action is not ready for execution"
            )
        now = clock.now()
        action.execution_started_at = now
        action.attempt_count += 1
        action.next_retry_at = None
        return None


def _record_execution_failure(
    database: Database,
    *,
    action_id: str,
    clock: Clock,
    error: ApplicationCoreError | SQLAlchemyError | RuntimeError | ValueError,
) -> None:
    with transaction(database) as session:
        action = find_pending_action(session, action_id)
        if action is None or action.state != PendingActionState.EXECUTING.value:
            return
        now = clock.now()
        _transition(action, PendingActionState.FAILED)
        action.last_error = safe_external_error(error)
        action.next_retry_at = (
            now + RETRY_DELAY if action.attempt_count < MAX_PENDING_ACTION_ATTEMPTS else None
        )


def execute_status_update(
    database: Database, *, action_id: str, clock: Clock, ids: IdGenerator
) -> ActionResult:
    """Execute one confirmed action atomically and idempotently."""
    claimed = _claim_status_update(database, action_id=action_id, clock=clock)
    if claimed is not None:
        return claimed
    try:
        return _execute_claimed_status_update(database, action_id=action_id, clock=clock, ids=ids)
    except (ApplicationCoreError, SQLAlchemyError, RuntimeError, ValueError) as error:
        _record_execution_failure(database, action_id=action_id, clock=clock, error=error)
        raise


def _execute_claimed_status_update(
    database: Database, *, action_id: str, clock: Clock, ids: IdGenerator
) -> ActionResult:
    with transaction(database) as session:
        action = find_pending_action(session, action_id)
        if action is None:
            raise PendingActionNotFoundError(action_id)
        if action.state != PendingActionState.EXECUTING.value:
            return ActionResult(
                action.id, PendingActionState(action.state), "action is not ready for execution"
            )
        now = clock.now()
        arguments = _arguments(action)
        reference = arguments.reference()
        application = find_application(session, action.user_id, reference)
        previous_status = application.current_status if application is not None else None
        previous_round = application.current_interview_round if application is not None else None
        changed = application is None or status_changed(
            ApplicationStatus(previous_status) if previous_status else None,
            previous_round,
            arguments.status,
            arguments.interview_round,
        )
        if application is None:
            application = Application(
                id=str(ids.new_id()),
                user_id=action.user_id,
                company=reference.company,
                department=reference.department,
                position=reference.position,
                company_key=reference.company_key,
                department_key=reference.department_key,
                position_key=reference.position_key,
                current_status=arguments.status.value,
                current_interview_round=arguments.interview_round,
                created_at=now,
                updated_at=now,
            )
            session.add(application)
        elif changed:
            application.current_status = arguments.status.value
            application.current_interview_round = arguments.interview_round
            application.updated_at = now
        if changed:
            event = JobEvent(
                id=str(ids.new_id()),
                application_id=application.id,
                pending_action_id=action.id,
                previous_status=previous_status,
                current_status=arguments.status.value,
                previous_interview_round=previous_round,
                current_interview_round=arguments.interview_round,
                confirmed_at=action.confirmed_at or now,
                created_at=now,
            )
            session.add(event)
            application.latest_job_event_id = event.id
            session.add(
                _status_event(
                    event_id=str(ids.new_id()),
                    application=application,
                    action=action,
                    previous_status=previous_status,
                    previous_round=previous_round,
                    now=now,
                )
            )
        _transition(action, PendingActionState.COMPLETED)
        action.completed_at = now
        return ActionResult(
            action.id,
            PendingActionState.COMPLETED,
            "application status updated" if changed else "application status unchanged",
            application.id,
            changed,
        )
