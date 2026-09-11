"""Durable completion and delivery state transitions for Agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobs_status_manager.agent.contracts import AgentRunState
from jobs_status_manager.agent.models import AgentRun, ConversationMessage
from jobs_status_manager.agent.models import Session as AgentSession
from jobs_status_manager.infrastructure.database.transactions import transaction

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from jobs_status_manager.agent.runtime import RuntimeServices
    from jobs_status_manager.agent.runtime_support import RunContext


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Persisted outcome of a QQ delivery attempt."""

    state: str | None = None
    error: str | None = None
    provider_message_id: str | None = None


def promote_next_run(session: Session, session_id: str, started_at: datetime) -> None:
    """Claim the oldest queued run after a terminal run completes."""
    next_run = session.scalar(
        select(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.state == AgentRunState.QUEUED.value,
        )
        .order_by(AgentRun.created_at)
    )
    if next_run is not None:
        next_run.state = AgentRunState.RUNNING.value
        next_run.started_at = started_at
        next_run.attempt_count += 1
        next_run.next_retry_at = None
        session_row = session.get(AgentSession, session_id)
        if session_row is not None:
            session_row.active_run_id = next_run.id


def persist_final_message(services: RuntimeServices, context: RunContext, answer: str) -> str:
    """Persist one assistant answer before external delivery."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, context.run_id)
        if run is None:
            return ""
        if run.final_message_id is not None:
            return run.final_message_id
        message_id = str(services.ids.new_id())
        session.add(
            ConversationMessage(
                id=message_id,
                session_id=context.session_id,
                role="assistant",
                content=answer,
                provider_event_id=None,
                created_at=services.clock.now(),
            )
        )
        session.flush()
        run.final_message_id = message_id
        run.state = AgentRunState.DELIVERY_PENDING.value
        run.completed_at = services.clock.now()
        return message_id


def record_delivery(
    services: RuntimeServices,
    context: RunContext,
    delivery: DeliveryOutcome,
) -> None:
    """Persist delivery and release the ordinary session claim."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, context.run_id)
        if run is None:
            return
        run.delivery_state = delivery.state
        run.delivery_error = delivery.error
        run.provider_message_id = delivery.provider_message_id
        run.state = AgentRunState.COMPLETED.value
        session_row = session.get(AgentSession, context.session_id)
        if session_row is not None:
            session_row.active_run_id = None
        promote_next_run(session, context.session_id, services.clock.now())


def record_command_delivery(
    services: RuntimeServices,
    context: RunContext,
    answer: str,
    delivery: DeliveryOutcome,
) -> None:
    """Complete a command without releasing the original session claim."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, context.run_id)
        if run is None:
            return
        message_id = str(services.ids.new_id())
        session.add(
            ConversationMessage(
                id=message_id,
                session_id=context.session_id,
                role="assistant",
                content=answer,
                provider_event_id=None,
                created_at=services.clock.now(),
            )
        )
        session.flush()
        run.final_message_id = message_id
        run.delivery_state = delivery.state
        run.delivery_error = delivery.error
        run.provider_message_id = delivery.provider_message_id
        run.state = AgentRunState.COMPLETED.value
        run.completed_at = services.clock.now()


def complete_run(
    services: RuntimeServices,
    context: RunContext,
    answer: str | None,
    error: str | None,
    delivery: DeliveryOutcome | None = None,
) -> None:
    """Persist a terminal run and release its session claim."""
    with transaction(services.database) as session:
        run = session.get(AgentRun, context.run_id)
        if run is None:
            return
        now = services.clock.now()
        outcome = delivery if delivery is not None else DeliveryOutcome()
        run.delivery_state = outcome.state
        run.delivery_error = outcome.error
        run.provider_message_id = outcome.provider_message_id
        session_row = session.get(AgentSession, context.session_id)
        if answer is None:
            run.state = AgentRunState.FAILED.value
            run.error = error or "agent run failed"
            run.completed_at = now
            if session_row is not None:
                session_row.active_run_id = None
            promote_next_run(session, context.session_id, now)
            return
        message_id = str(services.ids.new_id())
        session.add(
            ConversationMessage(
                id=message_id,
                session_id=context.session_id,
                role="assistant",
                content=answer,
                provider_event_id=None,
                created_at=now,
            )
        )
        session.flush()
        run.final_message_id = message_id
        run.state = AgentRunState.COMPLETED.value
        run.completed_at = now
        if session_row is not None:
            session_row.active_run_id = None
        promote_next_run(session, context.session_id, now)
