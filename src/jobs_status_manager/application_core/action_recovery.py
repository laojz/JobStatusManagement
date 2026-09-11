"""Recovery and controlled retry for confirmed status actions."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from jobs_status_manager.application_core.domain import PendingActionState
from jobs_status_manager.application_core.errors import (
    ApplicationCoreError,
    PendingActionNotFoundError,
)
from jobs_status_manager.application_core.execution import ActionResult, execute_status_update
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.application_core.proposals import ACTION_TYPE
from jobs_status_manager.application_core.repositories import find_pending_action
from jobs_status_manager.infrastructure.database.transactions import transaction

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


MAX_PENDING_ACTION_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)


def recover_pending_actions(
    database: Database,
    *,
    clock: Clock,
    ids: IdGenerator,
    stale_after: timedelta = timedelta(minutes=5),
) -> list[ActionResult]:
    """Recover confirmed, stale executing, and retryable failed actions."""
    now = clock.now()
    with transaction(database) as session:
        candidates = list(
            session.scalars(
                select(PendingAction).where(
                    PendingAction.action_type == ACTION_TYPE,
                    PendingAction.state.in_(
                        (
                            PendingActionState.CONFIRMED.value,
                            PendingActionState.EXECUTING.value,
                            PendingActionState.FAILED.value,
                        )
                    ),
                )
            )
        )
        for action in candidates:
            started = action.execution_started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=now.tzinfo)
            if (
                action.state == PendingActionState.EXECUTING.value
                and started is not None
                and started < now - stale_after
            ):
                action.state = PendingActionState.FAILED.value
                action.last_error = "stale execution recovered"
                action.next_retry_at = now + RETRY_DELAY
    with transaction(database) as session:
        candidates = list(
            session.scalars(
                select(PendingAction).where(
                    PendingAction.action_type == ACTION_TYPE,
                    (PendingAction.state == PendingActionState.CONFIRMED.value)
                    | (
                        (PendingAction.state == PendingActionState.FAILED.value)
                        & (PendingAction.attempt_count < MAX_PENDING_ACTION_ATTEMPTS)
                        & (
                            (PendingAction.next_retry_at.is_(None))
                            | (PendingAction.next_retry_at <= now)
                        )
                    ),
                )
            )
        )
    results: list[ActionResult] = []
    for action in candidates:
        try:
            results.append(
                execute_status_update(database, action_id=action.id, clock=clock, ids=ids)
            )
        except (ApplicationCoreError, SQLAlchemyError, RuntimeError, ValueError):
            continue
    return results


def retry_pending_action(database: Database, *, action_id: str, clock: Clock) -> ActionResult:
    """Explicitly requeue an existing failed action without executing it."""
    with transaction(database) as session:
        action = find_pending_action(session, action_id)
        if action is None:
            raise PendingActionNotFoundError(action_id)
        if action.state != PendingActionState.FAILED.value:
            return ActionResult(action.id, PendingActionState(action.state), "action is not failed")
        action.state = PendingActionState.CONFIRMED.value
        action.next_retry_at = clock.now()
        return ActionResult(action.id, PendingActionState.CONFIRMED, "pending action requeued")
