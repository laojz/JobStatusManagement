"""Durable task inspection and controlled retry operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, assert_never

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.agent.models import AgentRun
from jobs_status_manager.application.operation_contracts import (
    MAX_OPERATION_ERROR_CHARS,
    OperationResult,
    TaskKind,
    TaskRetryError,
    TaskSummary,
)
from jobs_status_manager.application_core.action_recovery import retry_pending_action
from jobs_status_manager.application_core.domain import PendingActionState
from jobs_status_manager.application_core.models import OutboxEvent, PendingAction
from jobs_status_manager.event_pipeline import retry_outbox_event
from jobs_status_manager.knowledge.contracts import KnowledgeLifecycle
from jobs_status_manager.knowledge.index import (
    IndexRetryServices,
    retry_cleanup,
    retry_index,
)
from jobs_status_manager.knowledge.models import KnowledgeDocument
from jobs_status_manager.notification_models import Notification
from jobs_status_manager.notifications import (
    NotificationServices,
    retry_failed_notification,
)

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator

STALE_AFTER = timedelta(minutes=5)


def _stale(started_at: datetime | None, now: datetime, state: str) -> bool:
    if started_at is None or state not in {"SENDING", "INDEXING", "EXECUTING", "RUNNING"}:
        return False
    normalized = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at
    return normalized <= now - STALE_AFTER


def _summary(task: TaskSummary, now: datetime) -> TaskSummary:
    return TaskSummary(
        task.kind,
        task.task_id,
        task.related_id,
        task.state,
        task.attempt_count,
        task.last_error[:MAX_OPERATION_ERROR_CHARS] if task.last_error else None,
        task.next_retry_at,
        task.started_at,
        _stale(task.started_at, now, task.state),
    )


def list_tasks(database: Database, *, include_stale: bool = False) -> tuple[TaskSummary, ...]:
    """List failed and optionally stale work without durable bodies."""
    now = datetime.now(UTC)
    results: list[TaskSummary] = []
    with Session(database.engine) as session:
        results.extend(
            _summary(
                TaskSummary(
                    TaskKind.OUTBOX,
                    row.id,
                    row.aggregate_id,
                    row.status,
                    row.attempt_count,
                    row.last_error,
                    row.next_attempt_at,
                    None,
                    stale=False,
                ),
                now,
            )
            for row in session.scalars(select(OutboxEvent).where(OutboxEvent.status == "FAILED"))
        )
        for row in session.scalars(
            select(Notification).where(Notification.state.in_(("FAILED", "SENDING")))
        ):
            task = _summary(
                TaskSummary(
                    TaskKind.NOTIFICATION,
                    row.id,
                    row.related_application_id,
                    row.state,
                    row.attempt_count,
                    row.last_error,
                    row.next_retry_at,
                    row.last_attempt_at,
                    stale=False,
                ),
                now,
            )
            if task.state == "FAILED" or (include_stale and task.stale):
                results.append(task)
        for row in session.scalars(
            select(AgentRun).where(AgentRun.state.in_(("FAILED", "RUNNING")))
        ):
            task = _summary(
                TaskSummary(
                    TaskKind.AGENT_RUN,
                    row.id,
                    row.session_id,
                    row.state,
                    row.attempt_count,
                    row.error or row.delivery_error,
                    row.next_retry_at,
                    row.started_at,
                    stale=False,
                ),
                now,
            )
            if task.state == "FAILED" or (include_stale and task.stale):
                results.append(task)
        for row in session.scalars(
            select(PendingAction).where(PendingAction.state.in_(("FAILED", "EXECUTING")))
        ):
            task = _summary(
                TaskSummary(
                    TaskKind.PENDING_ACTION,
                    row.id,
                    row.source_id,
                    row.state,
                    row.attempt_count,
                    row.last_error,
                    row.next_retry_at,
                    row.execution_started_at,
                    stale=False,
                ),
                now,
            )
            if task.state == "FAILED" or (include_stale and task.stale):
                results.append(task)
        for row in session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.index_status.in_(("FAILED", "INDEXING"))
            )
        ):
            kind = (
                TaskKind.KNOWLEDGE_INDEX
                if row.lifecycle_status == KnowledgeLifecycle.ACTIVE.value
                else TaskKind.KNOWLEDGE_CLEANUP
            )
            task = _summary(
                TaskSummary(
                    kind,
                    row.id,
                    row.source_file_id,
                    row.index_status,
                    row.index_attempt_count,
                    row.last_index_error,
                    None,
                    row.index_started_at,
                    stale=False,
                ),
                now,
            )
            if task.state == "FAILED" or (include_stale and task.stale):
                results.append(task)
    return tuple(sorted(results, key=lambda item: (item.kind.value, item.task_id)))


def retry_task(
    database: Database,
    kind: TaskKind,
    task_id: str,
    clock: Clock,
    ids: IdGenerator | None = None,
) -> OperationResult:
    """Retry one existing failed task without creating business facts."""
    match kind:
        case TaskKind.OUTBOX:
            changed = retry_outbox_event(database, task_id, clock)
            state, message = "PENDING", "outbox event requeued"
        case TaskKind.NOTIFICATION:
            if ids is None:
                message = "notification retry requires an ID generator"
                raise TaskRetryError(message)
            changed = retry_failed_notification(NotificationServices(database, clock, ids), task_id)
            state, message = "RETRY_WAIT", "notification requeued"
        case TaskKind.PENDING_ACTION:
            result = retry_pending_action(database, action_id=task_id, clock=clock)
            changed = result.state is PendingActionState.CONFIRMED
            state, message = result.state.value, result.message
        case TaskKind.KNOWLEDGE_INDEX:
            changed = retry_index(IndexRetryServices(database, clock), task_id)
            state, message = "PENDING", "knowledge index requeued"
        case TaskKind.KNOWLEDGE_CLEANUP:
            changed = retry_cleanup(IndexRetryServices(database, clock), task_id)
            state, message = (
                "RETRY_WAIT",
                "knowledge cleanup requeued; remains FAILED until deletion succeeds",
            )
        case TaskKind.AGENT_RUN:
            message = "agent runs do not have a safe manual retry operation"
            raise TaskRetryError(message)
        case unreachable:
            assert_never(unreachable)
    if not changed:
        message = f"retryable {kind.value} task not found: {task_id}"
        raise TaskRetryError(message)
    return OperationResult(task_id, kind, state, message)
