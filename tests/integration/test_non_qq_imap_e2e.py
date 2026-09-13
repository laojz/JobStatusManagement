from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text

from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.event_pipeline import EventServices, publish_once
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeIMAPGateway,
    FakeLLM,
    FakeQQGateway,
)
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.mail import (
    AnalysisConfidence,
    AnalysisDetails,
    ApplicationSuggestion,
    JobMailAnalysisInput,
    MailEnvelope,
    MailPollBatch,
    MailType,
    StatusSuggestion,
)
from jobs_status_manager.mail_poller import poll_once
from jobs_status_manager.mail_service import MailServices
from jobs_status_manager.notifications import NotificationServices, dispatch_once

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.identity.schemas import IdentitySummary
    from jobs_status_manager.infrastructure.clock import FakeClock
    from jobs_status_manager.infrastructure.database.connection import Database


def _setup(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> tuple[IdentitySummary, MailServices]:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    ids = DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 100)]
    )
    identity = bootstrap_identity(database, settings, fake_clock, ids)
    return identity, MailServices(database, fake_clock, ids)


def _envelope(provider_id: str, subject: str, content: str) -> MailEnvelope:
    return MailEnvelope(
        provider_message_id=provider_id,
        subject=subject,
        sender="hr@example.com",
        recipients=("me@example.com",),
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        content=content,
    )


def _analysis() -> JobMailAnalysisInput:
    return JobMailAnalysisInput(
        application=ApplicationSuggestion(company="腾讯", position="后端开发"),
        mail_type=MailType.APPLICATION,
        status_suggestion=StatusSuggestion(should_update=False),
        details=AnalysisDetails(),
        summary="申请已收到",
        confidence=AnalysisConfidence(application_match=0.99, status_suggestion=0.95),
    )


def test_non_qq_imap_business_path_is_deterministic_and_recoverable(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    identity, services = _setup(database, settings, fake_clock)
    job_message = _envelope("imap-uid-1", "application received", "申请已收到")
    informational_message = _envelope("imap-uid-2", "午餐菜单", "菜单更新")

    baseline = poll_once(
        services,
        identity.mail_account_id,
        FakeIMAPGateway(
            batch=MailPollBatch(envelopes=(), next_cursor="imap-v1-baseline", reset=True)
        ),
    )
    continued_gateway = FakeIMAPGateway(
        messages=[job_message, informational_message], next_cursor="imap-v1-page-2"
    )
    continued = poll_once(services, identity.mail_account_id, continued_gateway)
    duplicate_gateway = FakeIMAPGateway(
        messages=[job_message, informational_message], next_cursor="imap-v1-page-2"
    )
    duplicate = poll_once(services, identity.mail_account_id, duplicate_gateway)

    assert baseline.ingested == 0
    assert continued.ingested == 2
    assert duplicate.ingested == 0
    assert continued_gateway.calls == [("poll", ("test-account", "imap-v1-baseline"))]
    assert duplicate_gateway.calls == [("poll", ("test-account", "imap-v1-page-2"))]

    failed_llm = FakeLLM(error=RuntimeError("analysis provider unavailable"))
    publish_once(EventServices(services, failed_llm))
    with database.engine.connect() as connection:
        failed_mail = connection.execute(
            text(
                "SELECT processing_state, polling_cursor FROM mails "
                "JOIN mail_accounts ON mail_accounts.id = mails.mail_account_id "
                "WHERE mails.provider_message_id = 'imap-uid-1'"
            )
        ).one()
        assert failed_mail.processing_state == "RETRY_WAIT"
        assert failed_mail.polling_cursor == "imap-v1-page-2"
        assert connection.execute(text("SELECT COUNT(*) FROM mails")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM job_mail_analyses")).scalar_one() == 0

    fake_clock.advance(300)
    successful_llm = FakeLLM(analysis=_analysis())
    events = EventServices(services, successful_llm)
    publish_once(events)
    publish_once(events)
    qq = FakeQQGateway()
    dispatch_once(NotificationServices(database, fake_clock, services.ids), qq)

    assert len(successful_llm.calls) == 1
    assert len(qq.calls) == 1
    assert qq.calls[0][0] == "push"
    assert "腾讯 / 后端开发" in qq.calls[0][1][1]
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM mails")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM job_mail_analyses")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM notifications")).scalar_one() == 1
        assert connection.execute(text("SELECT state FROM notifications")).scalar_one() == "SENT"
        assert connection.execute(text("SELECT COUNT(*) FROM processed_events")).scalar_one() == 3
