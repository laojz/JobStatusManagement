"""Notification rendering, durable dispatch, retry, and stale recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobs_status_manager.agent.contracts import ProviderErrorKind
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.notification_models import Notification, NotificationAttempt

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.adapters.protocols import QQGateway
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


@unique
class NotificationState(StrEnum):
    """Durable notification lifecycle."""

    PENDING = "PENDING"
    SENDING = "SENDING"
    RETRY_WAIT = "RETRY_WAIT"
    SENT = "SENT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class NotificationServices:
    """Shared dispatcher dependencies."""

    database: Database
    clock: Clock
    ids: IdGenerator


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Normalized provider result."""

    success: bool
    provider_message_id: str | None
    error: str | None
    provider_error_kind: ProviderErrorKind | None = None


def ordinary_content(company: str, position: str, summary: str) -> str:
    """Render an ordinary job-mail notification."""
    return f"求职邮件: {company} / {position}\n{summary}"[:4000]


def confirmation_content(
    company: str, position: str, status: str, code: str, interview_round: int | None
) -> str:
    """Render a composite confirmation notification."""
    round_text = f" round={interview_round}" if interview_round is not None else ""
    return (
        f"状态建议: {company} / {position} -> {status}{round_text}\n"
        f"确认码: {code}\n未确认前不会修改 Application 或 JobEvent。"
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def recover_stale(
    services: NotificationServices, stale_after: timedelta = timedelta(minutes=5)
) -> int:
    """Return stale SENDING notifications to durable retry state."""
    now = services.clock.now()
    recovered = 0
    with transaction(services.database) as session:
        candidates = session.scalars(
            select(Notification).where(Notification.state == NotificationState.SENDING.value)
        )
        for notification in candidates:
            last_attempt = notification.last_attempt_at
            if last_attempt is None or _utc(last_attempt) <= now - stale_after:
                notification.state = NotificationState.RETRY_WAIT.value
                notification.next_retry_at = now
                notification.last_error = "stale sending recovered"
                notification.updated_at = now
                recovered += 1
    return recovered


def dispatch_once(services: NotificationServices, gateway: QQGateway, max_attempts: int = 3) -> int:
    """Dispatch all currently eligible notifications outside transactions."""
    recover_stale(services)
    now = services.clock.now()
    with transaction(services.database) as session:
        eligible = list(
            session.scalars(
                select(Notification).where(
                    (Notification.state == NotificationState.PENDING.value)
                    | (
                        (Notification.state == NotificationState.RETRY_WAIT.value)
                        & (
                            (Notification.next_retry_at.is_(None))
                            | (Notification.next_retry_at <= now)
                        )
                    )
                )
            )
        )
        claims = [(item.id, item.user_id, item.content) for item in eligible]
        for item in eligible:
            item.state = NotificationState.SENDING.value
            item.attempt_count += 1
            item.last_attempt_at = now
            item.updated_at = now
            session.add(
                NotificationAttempt(
                    id=str(services.ids.new_id()),
                    notification_id=item.id,
                    attempt_no=item.attempt_count,
                    result="STARTED",
                    started_at=now,
                    created_at=now,
                )
            )
    for notification_id, user_id, content in claims:
        try:
            result = gateway.push(user_id, content)
        except (RuntimeError, TimeoutError) as error:
            _finish_attempt(
                services,
                notification_id,
                DeliveryOutcome(
                    success=False,
                    provider_message_id=None,
                    error=safe_external_error(error),
                ),
                max_attempts=max_attempts,
            )
        else:
            _finish_attempt(
                services,
                notification_id,
                DeliveryOutcome(
                    success=result.success,
                    provider_message_id=result.provider_message_id,
                    error=None if result.success else "provider rejected push",
                    provider_error_kind=(
                        None
                        if result.success
                        else (
                            result.provider_error.kind
                            if result.provider_error is not None
                            else ProviderErrorKind.RETRYABLE
                        )
                    ),
                ),
                max_attempts=max_attempts,
            )
    return len(claims)


def _finish_attempt(
    services: NotificationServices,
    notification_id: str,
    outcome: DeliveryOutcome,
    *,
    max_attempts: int,
) -> None:
    now = services.clock.now()
    with transaction(services.database) as session:
        notification = session.get(Notification, notification_id)
        if notification is None:
            return
        attempt = session.scalar(
            select(NotificationAttempt).where(
                NotificationAttempt.notification_id == notification_id,
                NotificationAttempt.attempt_no == notification.attempt_count,
            )
        )
        if attempt is None:
            return
        attempt.completed_at = now
        attempt.provider_message_id = outcome.provider_message_id
        attempt.error = outcome.error
        if outcome.success:
            attempt.result = "SENT"
            notification.state = NotificationState.SENT.value
            notification.sent_at = now
            notification.provider_message_id = outcome.provider_message_id
            notification.last_error = None
            notification.next_retry_at = None
        else:
            attempt.result = (
                outcome.provider_error_kind.value
                if outcome.provider_error_kind is not None
                else "FAILED"
            )
            notification.last_error = (
                outcome.error if outcome.error is not None else "delivery failed"
            )
            if (
                outcome.provider_error_kind
                in (
                    ProviderErrorKind.PERMANENT,
                    ProviderErrorKind.DEFINITE,
                    ProviderErrorKind.AMBIGUOUS,
                )
                or notification.attempt_count >= max_attempts
            ):
                notification.state = NotificationState.FAILED.value
                notification.next_retry_at = None
            else:
                notification.state = NotificationState.RETRY_WAIT.value
                notification.next_retry_at = now + timedelta(minutes=5)
        notification.updated_at = now


def retry_failed_notification(services: NotificationServices, notification_id: str) -> bool:
    """Make one existing failed notification immediately eligible again."""
    now = services.clock.now()
    with transaction(services.database) as session:
        notification = session.get(Notification, notification_id)
        if notification is None or notification.state != NotificationState.FAILED.value:
            return False
        notification.state = NotificationState.RETRY_WAIT.value
        notification.next_retry_at = now
        notification.updated_at = now
        return True
