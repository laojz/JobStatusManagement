"""PendingAction proposal creation for status updates."""

import hashlib
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.application_core.domain import (
    ActionResolution,
    EventType,
    OutboxStatus,
    PendingActionState,
    StatusUpdateArguments,
    UserId,
    proposal_fingerprint,
)
from jobs_status_manager.application_core.models import OutboxEvent, PendingAction
from jobs_status_manager.application_core.repositories import confirmation_code_exists
from jobs_status_manager.infrastructure.clock import Clock
from jobs_status_manager.infrastructure.ids import IdGenerator

DEFAULT_EXPIRY_DAYS: Final = 7
ACTION_TYPE: Final = "UpdateApplicationStatus"
SOURCE_TYPE: Final = "local"
_CONFIRMATION_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _confirmation_code(action_id: str) -> str:
    """Derive a readable four-character code candidate from an action id."""
    value = int.from_bytes(hashlib.sha256(action_id.encode()).digest()[:8], "big")
    digits: list[str] = []
    for _ in range(4):
        value, remainder = divmod(value, len(_CONFIRMATION_ALPHABET))
        digits.append(_CONFIRMATION_ALPHABET[remainder])
    return "PA-" + "".join(reversed(digits))


def _event(  # noqa: PLR0913
    *,
    event_id: str,
    event_type: EventType,
    aggregate_type: str,
    aggregate_id: str,
    occurred_at: datetime,
    payload: dict[str, str | int | None],
    causation_id: str | None = None,
) -> OutboxEvent:
    """Build a Phase 1 outbox row."""
    return OutboxEvent(
        id=event_id,
        event_id=event_id,
        event_type=event_type.value,
        event_version=1,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        producer="application_core",
        payload=payload,
        causation_id=causation_id,
        occurred_at=occurred_at,
        status=OutboxStatus.PENDING.value,
        created_at=occurred_at,
        attempt_count=0,
    )


def create_status_proposal(  # noqa: PLR0913
    session: Session,
    *,
    user_id: UserId,
    source_id: str,
    arguments: StatusUpdateArguments,
    clock: Clock,
    ids: IdGenerator,
    source_type: str = SOURCE_TYPE,
    mail_analysis_id: str | None = None,
) -> PendingAction:
    """Persist a frozen status proposal without changing business facts."""
    fingerprint = proposal_fingerprint(arguments)
    existing = session.scalar(
        select(PendingAction).where(
            PendingAction.user_id == user_id,
            PendingAction.source_type == source_type,
            PendingAction.source_id == source_id,
            PendingAction.action_type == ACTION_TYPE,
            PendingAction.proposal_fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return existing

    now = clock.now()
    action_id = str(ids.new_id())
    confirmation_code = _confirmation_code(action_id)
    while confirmation_code_exists(session, confirmation_code):
        confirmation_code = _confirmation_code(str(ids.new_id()))
    reference = arguments.reference()
    summary = (
        f"{reference.company} / {reference.department or '无部门'} / {reference.position} "
        f"→ {arguments.status.value}"
    )
    action = PendingAction(
        id=action_id,
        user_id=user_id,
        source_type=source_type,
        source_id=source_id,
        action_type=ACTION_TYPE,
        resolved_arguments={
            "company": arguments.company,
            "department": arguments.department,
            "position": arguments.position,
            "status": arguments.status.value,
            "interview_round": arguments.interview_round,
        },
        display_summary=summary,
        proposal_fingerprint=fingerprint,
        confirmation_code=confirmation_code,
        state=PendingActionState.PENDING.value,
        created_at=now,
        expires_at=now + timedelta(days=DEFAULT_EXPIRY_DAYS),
        attempt_count=0,
    )
    action.mail_analysis_id = mail_analysis_id
    session.add(action)
    session.add(
        _event(
            event_id=str(ids.new_id()),
            event_type=EventType.PENDING_ACTION_CREATED,
            aggregate_type="PendingAction",
            aggregate_id=action.id,
            occurred_at=now,
            payload={"confirmation_code": action.confirmation_code, "summary": summary},
        )
    )
    return action


def resolved_event(
    *,
    action: PendingAction,
    event_id: str,
    occurred_at: datetime,
    resolution: ActionResolution,
) -> OutboxEvent:
    """Build the outbox event describing a user resolution."""
    return _event(
        event_id=event_id,
        event_type=EventType.PENDING_ACTION_RESOLVED,
        aggregate_type="PendingAction",
        aggregate_id=action.id,
        occurred_at=occurred_at,
        payload={"resolution": resolution.value, "state": action.state},
    )
