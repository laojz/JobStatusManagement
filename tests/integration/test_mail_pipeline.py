from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from jobs_status_manager.agent.contracts import ProviderError, ProviderErrorKind
from jobs_status_manager.application_core.domain import ApplicationStatus, UserId
from jobs_status_manager.application_core.models import OutboxEvent
from jobs_status_manager.application_core.service import execute_status_update, resolve_confirmation
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.event_pipeline import (
    EventServices,
    publish_once,
    retry_outbox_event,
)
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeIMAPGateway,
    FakeLLM,
    FakeQQDeliveryResult,
    FakeQQGateway,
)
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.mail import (
    AnalysisConfidence,
    AnalysisDetails,
    ApplicationSuggestion,
    JobMailAnalysisInput,
    MailEnvelope,
    MailType,
    StatusSuggestion,
)
from jobs_status_manager.mail_models import ProcessedEvent
from jobs_status_manager.mail_poller import poll_once
from jobs_status_manager.mail_service import MailServices
from jobs_status_manager.notification_models import Notification, NotificationAttempt
from jobs_status_manager.notifications import (
    NotificationServices,
    dispatch_once,
    recover_stale,
    retry_failed_notification,
)

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.identity.schemas import IdentitySummary
    from jobs_status_manager.infrastructure.clock import FakeClock
    from jobs_status_manager.infrastructure.database.connection import Database


@dataclass(frozen=True, slots=True)
class PipelineSetup:
    identity: IdentitySummary
    services: MailServices


def _ids() -> DeterministicIdGenerator:
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 100)]
    )


def _setup(database: Database, settings: AppSettings, fake_clock: FakeClock) -> PipelineSetup:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    ids = _ids()
    identity = bootstrap_identity(database, settings, fake_clock, ids)
    return PipelineSetup(identity, MailServices(database, fake_clock, ids))


def _envelope(provider_id: str, subject: str, content: str) -> MailEnvelope:
    return MailEnvelope(
        provider_message_id=provider_id,
        subject=subject,
        sender="hr@example.com",
        recipients=("me@example.com",),
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        content=content,
    )


def _analysis(*, should_update: bool) -> JobMailAnalysisInput:
    return JobMailAnalysisInput(
        application=ApplicationSuggestion(company="腾讯", position="后端开发"),
        mail_type=MailType.INTERVIEW,
        status_suggestion=StatusSuggestion(
            should_update=should_update,
            status=ApplicationStatus.INTERVIEW if should_update else None,
            interview_round=2 if should_update else None,
        ),
        details=AnalysisDetails(),
        summary="腾讯二面邀请" if should_update else "面试安排信息",
        confidence=AnalysisConfidence(application_match=0.99, status_suggestion=0.95),
    )


def _poll(setup: PipelineSetup, message: MailEnvelope) -> None:
    poll_once(
        setup.services,
        setup.identity.mail_account_id,
        FakeIMAPGateway(messages=[message]),
    )


def test_poll_passes_cursor_and_counts_only_new_mail(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    message = _envelope("m-cursor", "application received", "申请已收到")
    first_gateway = FakeIMAPGateway(messages=[message])
    first = poll_once(setup.services, setup.identity.mail_account_id, first_gateway)
    second_gateway = FakeIMAPGateway(messages=[message])
    second = poll_once(setup.services, setup.identity.mail_account_id, second_gateway)

    assert first.ingested == 1
    assert second.ingested == 0
    assert first_gateway.calls == [("poll", ("test-account", ""))]
    assert second_gateway.calls == [("poll", ("test-account", "m-cursor"))]


def test_poll_failure_keeps_cursor_and_records_error(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    message = _envelope("m-before-failure", "application received", "申请已收到")
    poll_once(
        setup.services,
        setup.identity.mail_account_id,
        FakeIMAPGateway(messages=[message]),
    )
    failed = poll_once(
        setup.services,
        setup.identity.mail_account_id,
        FakeIMAPGateway(error=RuntimeError("imap unavailable")),
    )

    assert failed.failed is True
    with database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT polling_cursor, polling_last_error FROM mail_accounts WHERE id=:account_id"
            ),
            {"account_id": setup.identity.mail_account_id},
        ).one()
        assert row.polling_cursor == "m-before-failure"
        assert row.polling_last_error == safe_external_error(RuntimeError("imap unavailable"))


def test_ingestion_is_idempotent_and_non_job_stops(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    message = _envelope("m-1", "午餐菜单", "菜单更新")
    _poll(setup, message)
    _poll(setup, message)
    llm = FakeLLM(analysis=_analysis(should_update=False))
    publish_once(EventServices(setup.services, llm))
    assert len(llm.calls) == 0
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM mails")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM outbox_events WHERE event_type='MAIL_RECEIVED'")
            ).scalar_one()
            == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM job_mail_analyses")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM notifications")).scalar_one() == 0


def test_ordinary_job_mail_reaches_qq_once(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-2", "application received", "你的申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    publish_once(events)
    qq = FakeQQGateway()
    dispatch_once(NotificationServices(database, fake_clock, setup.services.ids), qq)
    assert len(qq.calls) == 1
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM notifications")).scalar_one() == "SENT"
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0


def test_notification_success_claim_is_idempotent(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-duplicate-claim", "application received", "你的申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    notifications = NotificationServices(database, fake_clock, setup.services.ids)
    gateway = FakeQQGateway()

    assert dispatch_once(notifications, gateway) == 1
    assert dispatch_once(notifications, gateway) == 0
    assert gateway.calls == [
        ("push", (setup.identity.user_id, "求职邮件: 腾讯 / 后端开发\n面试安排信息"))
    ]


def test_status_mail_creates_inert_action_and_confirmation(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-3", "腾讯二面邀请", "请参加二面"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=True)))
    for _ in range(3):
        publish_once(events)
    qq = FakeQQGateway()
    dispatch_once(NotificationServices(database, fake_clock, setup.services.ids), qq)
    content = qq.calls[0][1][1]
    assert "腾讯" in content
    assert "PA-" in content
    assert "不会修改" in content
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0


def test_analysis_failure_persists_retry_without_effect(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-4", "application received", "申请已收到"))
    llm = FakeLLM(error=RuntimeError("provider unavailable"))
    publish_once(EventServices(setup.services, llm))
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT processing_state FROM mails")).scalar_one()
            == "RETRY_WAIT"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM job_mail_analyses")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT last_error FROM outbox_events")).scalar_one()
            is not None
        )


def test_missing_outbox_aggregate_remains_retryable(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-missing", "application received", "申请已收到"))
    with transaction(database) as session:
        event = session.scalars(select(OutboxEvent)).one()
        event.aggregate_id = "missing-mail"

    publish_once(EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False))))

    with database.engine.connect() as connection:
        event = connection.execute(
            text("SELECT status, attempt_count, last_error FROM outbox_events")
        ).one()
        assert event.status == "PENDING"
        assert event.attempt_count == 1
        assert "missing-mail" in event.last_error


def test_outbox_failure_becomes_failed_and_manual_retry_preserves_event(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-outbox-failed", "application received", "申请已收到"))
    with transaction(database) as session:
        event = session.scalars(select(OutboxEvent)).one()
        event.aggregate_id = "x" * 5000

    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    for _ in range(3):
        publish_once(events)
        fake_clock.advance(300)

    with database.engine.connect() as connection:
        event = connection.execute(
            text("SELECT id, event_id, status, attempt_count, last_error FROM outbox_events")
        ).one()
        assert event.status == "FAILED"
        assert event.attempt_count == 3
        assert len(event.last_error) == 2000

    assert retry_outbox_event(database, event.id, fake_clock) is True
    with database.engine.connect() as connection:
        retried = connection.execute(
            text("SELECT event_id, status, attempt_count, next_attempt_at FROM outbox_events")
        ).one()
        assert retried.event_id == event.event_id
        assert retried.status == "PENDING"
        assert retried.attempt_count == 3
        assert retried.next_attempt_at == "2026-01-01 00:15:00.000000"


def test_outbox_manual_retry_requires_existing_failed_event(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-outbox-pending", "application received", "申请已收到"))

    with database.engine.connect() as connection:
        event_id = connection.execute(text("SELECT id FROM outbox_events")).scalar_one()

    assert retry_outbox_event(database, event_id, fake_clock) is False
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT status FROM outbox_events")).scalar_one() == "PENDING"
        )


def test_duplicate_events_and_notification_recovery(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-5", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    for _ in range(3):
        publish_once(events)
    failed = FakeQQGateway(delivery=FakeQQDeliveryResult(success=False, provider_message_id=None))
    notifications = NotificationServices(database, fake_clock, setup.services.ids)
    dispatch_once(notifications, failed)
    with transaction(database) as session:
        notification = session.scalars(select(Notification)).one()
        notification.state = "SENDING"
        notification.last_attempt_at = fake_clock.now() - timedelta(minutes=10)
    assert recover_stale(notifications) == 1
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM notifications")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT state FROM notifications")).scalar_one() == "RETRY_WAIT"
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM notification_attempts")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM processed_events")).scalar_one() == 2


def test_notification_reaches_failed_after_attempt_limit(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-delivery-fails", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    notifications = NotificationServices(database, fake_clock, setup.services.ids)
    failed_gateway = FakeQQGateway(
        delivery=FakeQQDeliveryResult(success=False, provider_message_id=None)
    )

    for _ in range(3):
        dispatch_once(notifications, failed_gateway)
        fake_clock.advance(300)

    with database.engine.connect() as connection:
        notification = connection.execute(
            text("SELECT state, attempt_count FROM notifications")
        ).one()
        assert notification.state == "FAILED"
        assert notification.attempt_count == 3
        assert (
            connection.execute(text("SELECT COUNT(*) FROM notification_attempts")).scalar_one() == 3
        )


def test_notification_attempt_fields_are_persisted_coherently(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-attempt-fields", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    notification_services = NotificationServices(database, fake_clock, setup.services.ids)

    dispatch_once(notification_services, FakeQQGateway())

    with transaction(database) as session:
        attempt = session.scalar(select(NotificationAttempt))
        notification = session.scalar(select(Notification))
        assert attempt is not None
        assert notification is not None
        assert attempt.notification_id == notification.id
        assert attempt.attempt_no == 1
        assert attempt.result == "SENT"
        assert attempt.provider_message_id == "fake-message"
        assert attempt.started_at is not None
        assert attempt.completed_at is not None
        assert attempt.error is None


def test_notification_attempt_failure_error_is_bounded_and_safe(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-attempt-error", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    error = RuntimeError("https://api.example.test/push?token=secret body=private " + "x" * 2100)

    dispatch_once(
        NotificationServices(database, fake_clock, setup.services.ids),
        FakeQQGateway(error=error),
    )

    with transaction(database) as session:
        attempt = session.scalar(select(NotificationAttempt))
        assert attempt is not None
        assert attempt.result == "FAILED"
        assert attempt.provider_message_id is None
        assert attempt.started_at is not None
        assert attempt.completed_at is not None
        assert attempt.error is not None
        assert len(attempt.error) <= 2000
        assert "secret" not in attempt.error
        assert "private" not in attempt.error


@pytest.mark.parametrize(
    "case",
    [
        (ProviderErrorKind.RETRYABLE, "RETRY_WAIT", True),
        (ProviderErrorKind.PERMANENT, "FAILED", False),
        (ProviderErrorKind.AMBIGUOUS, "FAILED", False),
    ],
)
def test_notification_provider_classification_controls_retry(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    case: tuple[ProviderErrorKind, str, bool],
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-classified", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    gateway = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(kind=case[0], provider_name="qq", code="test"),
        )
    )
    notifications = NotificationServices(database, fake_clock, setup.services.ids)

    assert dispatch_once(notifications, gateway) == 1
    with database.engine.connect() as connection:
        state = connection.execute(text("SELECT state FROM notifications")).scalar_one()
        attempt_result = connection.execute(
            text("SELECT result FROM notification_attempts")
        ).scalar_one()
    assert state == case[1]
    assert attempt_result == (case[0].value if case[0] is not None else "FAILED")
    if not case[2]:
        assert dispatch_once(notifications, gateway) == 0


def test_processed_event_unique_constraint_rejects_duplicate_marker(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    with transaction(database) as session:
        session.add(
            ProcessedEvent(
                id=str(setup.services.ids.new_id()),
                consumer_name="test-consumer",
                event_id="event-1",
                processed_at=fake_clock.now(),
            )
        )
    with pytest.raises(IntegrityError), transaction(database) as session:
        session.add(
            ProcessedEvent(
                id=str(setup.services.ids.new_id()),
                consumer_name="test-consumer",
                event_id="event-1",
                processed_at=fake_clock.now(),
            )
        )


def test_failed_notification_manual_retry_preserves_attempt_history(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-notification-retry", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    notifications = NotificationServices(database, fake_clock, setup.services.ids)
    failed_gateway = FakeQQGateway(
        delivery=FakeQQDeliveryResult(success=False, provider_message_id=None)
    )
    for _ in range(3):
        dispatch_once(notifications, failed_gateway)
        fake_clock.advance(300)

    with database.engine.connect() as connection:
        notification_id = connection.execute(text("SELECT id FROM notifications")).scalar_one()
        assert connection.execute(text("SELECT attempt_count FROM notifications")).scalar_one() == 3

    assert retry_failed_notification(notifications, notification_id) is True
    with database.engine.connect() as connection:
        notification = connection.execute(
            text("SELECT state, attempt_count, next_retry_at, last_error FROM notifications")
        ).one()
        assert notification.state == "RETRY_WAIT"
        assert notification.attempt_count == 3
        assert notification.next_retry_at == "2026-01-01 00:15:00.000000"
        assert notification.last_error == "provider rejected push"
        assert (
            connection.execute(text("SELECT COUNT(*) FROM notification_attempts")).scalar_one() == 3
        )

    successful_gateway = FakeQQGateway()
    assert dispatch_once(notifications, successful_gateway) == 1
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM notifications")).scalar_one() == "SENT"
        assert (
            connection.execute(text("SELECT COUNT(*) FROM notification_attempts")).scalar_one() == 4
        )


def test_stale_sending_without_attempt_timestamp_is_recovered(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-stale-null", "application received", "申请已收到"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=False)))
    publish_once(events)
    publish_once(events)
    with transaction(database) as session:
        notification = session.scalars(select(Notification)).one()
        notification.state = "SENDING"
        notification.last_attempt_at = None

    assert recover_stale(NotificationServices(database, fake_clock, setup.services.ids)) == 1
    with database.engine.connect() as connection:
        notification = connection.execute(
            text("SELECT state, next_retry_at, last_error FROM notifications")
        ).one()
        assert notification.state == "RETRY_WAIT"
        assert notification.next_retry_at == "2026-01-01 00:00:00.000000"
        assert notification.last_error == "stale sending recovered"


def test_confirmed_mail_action_reuses_phase_one_execution(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    setup = _setup(database, settings, fake_clock)
    _poll(setup, _envelope("m-6", "腾讯二面邀请", "请参加二面"))
    events = EventServices(setup.services, FakeLLM(analysis=_analysis(should_update=True)))
    publish_once(events)
    publish_once(events)
    with database.engine.connect() as connection:
        code = connection.execute(
            text("SELECT confirmation_code FROM pending_actions")
        ).scalar_one()
    result = resolve_confirmation(
        database,
        user_id=UserId(setup.identity.user_id),
        command_text=f"确认 {code}",
        clock=fake_clock,
        ids=setup.services.ids,
    )
    assert result is not None
    execution = execute_status_update(
        database,
        action_id=result.action_id,
        clock=fake_clock,
        ids=setup.services.ids,
    )
    assert execution.changed is True
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
