"""Session-bound repositories for the Phase 1 application core."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.application_core.domain import ApplicationReference
from jobs_status_manager.application_core.models import (
    Application,
    JobEvent,
    OutboxEvent,
    PendingAction,
)


def find_application(
    session: Session, user_id: str, reference: ApplicationReference
) -> Application | None:
    """Find an application by its normalized exact business key."""
    return session.scalar(
        select(Application).where(
            Application.user_id == user_id,
            Application.company_key == reference.company_key,
            Application.department_key == reference.department_key,
            Application.position_key == reference.position_key,
        )
    )


def find_pending_action(session: Session, action_id: str) -> PendingAction | None:
    """Find an action by its primary key."""
    return session.get(PendingAction, action_id)


def confirmation_code_exists(session: Session, confirmation_code: str) -> bool:
    """Report whether a confirmation code is already allocated."""
    return (
        session.scalar(
            select(PendingAction.id).where(PendingAction.confirmation_code == confirmation_code)
        )
        is not None
    )


def find_pending_by_code(
    session: Session, user_id: str, confirmation_code: str
) -> PendingAction | None:
    """Find an action by code while enforcing user ownership."""
    return session.scalar(
        select(PendingAction).where(
            PendingAction.user_id == user_id,
            PendingAction.confirmation_code == confirmation_code,
        )
    )


def list_pending_actions(session: Session, user_id: str, now: datetime) -> list[PendingAction]:
    """List unexpired actions awaiting confirmation for one user."""
    return list(
        session.scalars(
            select(PendingAction)
            .where(
                PendingAction.user_id == user_id,
                PendingAction.state == "PENDING",
                PendingAction.expires_at > now,
            )
            .order_by(PendingAction.created_at)
        )
    )


def create_outbox_event(session: Session, event: OutboxEvent) -> None:
    """Add one durable outbox envelope to the current transaction."""
    session.add(event)


def find_job_event(session: Session, pending_action_id: str) -> JobEvent | None:
    """Find the business result produced by an action."""
    return session.scalar(select(JobEvent).where(JobEvent.pending_action_id == pending_action_id))
