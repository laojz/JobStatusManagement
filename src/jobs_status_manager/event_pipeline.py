"""At-least-once outbox publication with idempotent persisted consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, assert_never

from sqlalchemy import select

from jobs_status_manager.application_core.domain import (
    EventType,
    OutboxStatus,
)
from jobs_status_manager.application_core.models import OutboxEvent
from jobs_status_manager.event_consumers import (
    consume_action_created,
    consume_job_analyzed,
    consume_mail_received,
)
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.adapters.protocols import LLMAdapter
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.mail_service import MailServices


@dataclass(frozen=True, slots=True)
class EventServices:
    """Outbox and consumer dependencies."""

    mail: MailServices
    llm: LLMAdapter


MAX_OUTBOX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)


def publish_once(services: EventServices, max_attempts: int = MAX_OUTBOX_ATTEMPTS) -> int:
    """Deliver eligible outbox rows and persist publisher retry state."""
    now = services.mail.clock.now()
    with transaction(services.mail.database) as session:
        events = list(
            session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OutboxStatus.PENDING.value,
                    (OutboxEvent.next_attempt_at.is_(None)) | (OutboxEvent.next_attempt_at <= now),
                )
                .order_by(OutboxEvent.created_at)
            )
        )
        event_ids = [event.id for event in events]
    for event_id in event_ids:
        try:
            _deliver(services, event_id)
        except (RuntimeError, ValueError) as error:
            with transaction(services.mail.database) as session:
                event = session.get(OutboxEvent, event_id)
                if event is not None:
                    event.attempt_count += 1
                    event.last_error = safe_external_error(error)
                    if event.attempt_count >= max_attempts:
                        event.status = OutboxStatus.FAILED.value
                        event.next_attempt_at = None
                    else:
                        event.next_attempt_at = now + RETRY_DELAY
        else:
            with transaction(services.mail.database) as session:
                event = session.get(OutboxEvent, event_id)
                if event is not None:
                    event.attempt_count += 1
                    event.status = OutboxStatus.PUBLISHED.value
                    event.published_at = services.mail.clock.now()
                    event.last_error = None
                    event.next_attempt_at = None
    return len(event_ids)


def retry_outbox_event(database: Database, event_id: str, clock: Clock) -> bool:
    """Make one existing terminal outbox event immediately eligible again."""
    with transaction(database) as session:
        event = session.get(OutboxEvent, event_id)
        if event is None or event.status != OutboxStatus.FAILED.value:
            return False
        event.status = OutboxStatus.PENDING.value
        event.next_attempt_at = clock.now()
        return True


def _deliver(services: EventServices, event_id: str) -> None:
    with transaction(services.mail.database) as session:
        event = session.get(OutboxEvent, event_id)
        if event is None:
            message = f"outbox event not found: {event_id}"
            raise RuntimeError(message)
        event_type = EventType(event.event_type)
        aggregate_id = event.aggregate_id
    match event_type:
        case EventType.MAIL_RECEIVED:
            consume_mail_received(services, event_id, aggregate_id)
        case EventType.JOB_MAIL_ANALYZED:
            consume_job_analyzed(services, event_id, aggregate_id)
        case EventType.PENDING_ACTION_CREATED:
            consume_action_created(services, event_id, aggregate_id)
        case (
            EventType.PENDING_ACTION_RESOLVED
            | EventType.APPLICATION_STATUS_CHANGED
            | EventType.KNOWLEDGE_DOCUMENT_ADDED
            | EventType.KNOWLEDGE_DOCUMENT_REMOVED
        ):
            return
        case unreachable:
            assert_never(unreachable)
