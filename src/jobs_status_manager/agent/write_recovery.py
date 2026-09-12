"""Recovery scans for confirmation-gated Agent writes."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from sqlalchemy import or_, select

from jobs_status_manager.agent.contracts import AgentRunState, ProviderErrorKind
from jobs_status_manager.agent.models import AgentRun, ConversationMessage
from jobs_status_manager.agent.models import Session as AgentSession
from jobs_status_manager.agent.write_runtime import (
    finalize_rejected_action,
    record_confirmation_delivery,
    resume_confirmed_action,
)
from jobs_status_manager.application_core.domain import PendingActionState
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime import RuntimeServices


def retry_confirmation_prompts(services: RuntimeServices) -> int:
    """Retry persisted prompts for waiting runs whose delivery is incomplete."""
    now = services.clock.now()
    with transaction(services.database) as session:
        runs = list(
            session.scalars(
                select(AgentRun).where(
                    AgentRun.state == AgentRunState.WAITING_USER_CONFIRMATION.value,
                    or_(
                        AgentRun.delivery_state.is_(None),
                        AgentRun.delivery_state == "FAILED",
                        AgentRun.delivery_state == "RETRY_WAIT",
                    ),
                    or_(AgentRun.next_retry_at.is_(None), AgentRun.next_retry_at <= now),
                    AgentRun.attempt_count < services.max_delivery_attempts,
                )
            )
        )
        prompts = [
            (
                run.id,
                session.scalar(
                    select(ConversationMessage.content).where(
                        ConversationMessage.id == run.final_message_id
                    )
                ),
                session.scalar(
                    select(AgentSession.user_id).where(AgentSession.id == run.session_id)
                ),
            )
            for run in runs
        ]
        for run in runs:
            run.attempt_count += 1
            run.next_retry_at = None
    attempted = 0
    for run_id, prompt, user_id in prompts:
        if prompt is None or user_id is None:
            continue
        try:
            delivery = services.qq.push(user_id, prompt)
        except (RuntimeError, ValueError, TimeoutError) as exception:
            record_confirmation_delivery(
                services,
                run_id,
                success=False,
                error=safe_external_error(exception),
                provider_error_kind=ProviderErrorKind.AMBIGUOUS,
            )
        else:
            record_confirmation_delivery(
                services,
                run_id,
                success=delivery.success,
                error=None if delivery.success else "QQ provider rejected delivery",
                provider_error_kind=(
                    None
                    if delivery.success or delivery.provider_error is None
                    else delivery.provider_error.kind
                ),
            )
        attempted += 1
    return attempted


def reconcile_terminal_actions(services: RuntimeServices) -> tuple[str, ...]:
    """Resume waiting runs whose action resolution already committed."""
    with transaction(services.database) as session:
        actions = list(
            session.scalars(
                select(PendingAction)
                .join(AgentRun, PendingAction.agent_run_id == AgentRun.id)
                .where(
                    PendingAction.state.in_(
                        (PendingActionState.COMPLETED.value, PendingActionState.REJECTED.value)
                    ),
                    AgentRun.state == AgentRunState.WAITING_USER_CONFIRMATION.value,
                )
            )
        )
    run_ids: list[str] = []
    for action in actions:
        state = PendingActionState(action.state)
        match state:
            case PendingActionState.COMPLETED:
                run_id = resume_confirmed_action(services, action.id)
            case PendingActionState.REJECTED:
                run_id = finalize_rejected_action(
                    services.database,
                    action.id,
                    services.clock,
                    services.ids,
                )
            case (
                PendingActionState.PENDING
                | PendingActionState.CONFIRMED
                | PendingActionState.EXECUTING
                | PendingActionState.FAILED
                | PendingActionState.EXPIRED
            ):
                continue
            case unreachable:
                assert_never(unreachable)
        if run_id is not None:
            run_ids.append(run_id)
    return tuple(run_ids)
