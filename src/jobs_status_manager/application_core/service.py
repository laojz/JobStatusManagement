"""Application Core proposal and confirmation services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from jobs_status_manager.application_core import execution as _execution
from jobs_status_manager.application_core.action_recovery import (
    recover_pending_actions,
    retry_pending_action,
)
from jobs_status_manager.application_core.domain import (
    ActionResolution,
    PendingActionState,
    StatusUpdateArguments,
    UserId,
)
from jobs_status_manager.application_core.errors import (
    AmbiguousPendingActionError,
    PendingActionNotFoundError,
)
from jobs_status_manager.application_core.proposals import (
    create_status_proposal,
    resolved_event,
)
from jobs_status_manager.application_core.repositories import (
    find_pending_by_code,
    list_pending_actions,
)
from jobs_status_manager.application_core.router import route_command
from jobs_status_manager.infrastructure.database.transactions import transaction

ActionResult = _execution.ActionResult
execute_status_update = _execution.execute_status_update

__all__ = [
    "ActionResult",
    "execute_status_update",
    "propose_status_update",
    "recover_pending_actions",
    "resolve_confirmation",
    "retry_pending_action",
]

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from jobs_status_manager.application_core.models import PendingAction
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


def _utc(value: datetime) -> datetime:
    """Treat SQLite's timezone-stripped values as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def propose_status_update(  # noqa: PLR0913
    database: Database,
    *,
    user_id: UserId,
    source_id: str,
    arguments: StatusUpdateArguments,
    clock: Clock,
    ids: IdGenerator,
) -> PendingAction:
    """Create a proposal through the transaction helper."""
    with transaction(database) as session:
        return create_status_proposal(
            session,
            user_id=user_id,
            source_id=source_id,
            arguments=arguments,
            clock=clock,
            ids=ids,
        )


def _resolve_action(  # noqa: PLR0913
    session: Session,
    *,
    user_id: UserId,
    resolution: ActionResolution,
    confirmation_code: str | None,
    clock: Clock,
    ids: IdGenerator,
) -> ActionResult:
    """Resolve one action in a short transaction."""
    now = clock.now()
    if confirmation_code is None:
        actions = list_pending_actions(session, user_id, now)
        if len(actions) != 1:
            raise AmbiguousPendingActionError(len(actions))
        action = actions[0]
    else:
        action = find_pending_by_code(session, user_id, confirmation_code)
        if action is None:
            raise PendingActionNotFoundError(confirmation_code)
    if _utc(action.expires_at) <= now and action.state == PendingActionState.PENDING.value:
        action.state = PendingActionState.EXPIRED.value
        action.last_error = "confirmation expired"
        return ActionResult(action.id, PendingActionState.EXPIRED, "pending action expired")
    if action.state != PendingActionState.PENDING.value:
        return ActionResult(
            action.id,
            PendingActionState(action.state),
            "pending action already processed",
        )
    target = (
        PendingActionState.CONFIRMED
        if resolution is ActionResolution.CONFIRM
        else PendingActionState.REJECTED
    )
    action.state = target.value
    if target is PendingActionState.CONFIRMED:
        action.confirmed_at = now
    else:
        action.rejected_at = now
    session.add(
        resolved_event(
            action=action,
            event_id=str(ids.new_id()),
            occurred_at=now,
            resolution=resolution,
        )
    )
    message = (
        "pending action confirmed"
        if target is PendingActionState.CONFIRMED
        else "pending action rejected"
    )
    return ActionResult(action.id, target, message)


def resolve_confirmation(
    database: Database,
    *,
    user_id: UserId,
    command_text: str,
    clock: Clock,
    ids: IdGenerator,
) -> ActionResult | None:
    """Route and resolve a confirmation command, or return None for ordinary text."""
    command = route_command(command_text)
    if command.resolution is None:
        return None
    with transaction(database) as session:
        return _resolve_action(
            session,
            user_id=user_id,
            resolution=command.resolution,
            confirmation_code=command.confirmation_code,
            clock=clock,
            ids=ids,
        )
