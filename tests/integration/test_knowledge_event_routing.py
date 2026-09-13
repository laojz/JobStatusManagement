from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from jobs_status_manager.application_core.domain import UserId
from jobs_status_manager.application_core.models import OutboxEvent, PendingAction
from jobs_status_manager.application_core.service import resolve_confirmation
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.event_consumers import consume_action_created
from jobs_status_manager.event_pipeline import EventServices, publish_once
from jobs_status_manager.infrastructure.adapters.fakes import FakeLLM
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.knowledge.contracts import (
    AddKnowledgeArguments,
    DocumentType,
    RemoveKnowledgeArguments,
)
from jobs_status_manager.knowledge.dependencies import KnowledgeServices
from jobs_status_manager.knowledge.execution import execute_add
from jobs_status_manager.knowledge.models import UserFile
from jobs_status_manager.knowledge.proposals import propose_add, propose_remove
from jobs_status_manager.mail_models import ProcessedEvent
from jobs_status_manager.mail_service import MailServices
from jobs_status_manager.notification_models import Notification


def _ids() -> DeterministicIdGenerator:
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 100)]
    )


def _create_document(
    database: Database,
    settings: AppSettings,
    clock: FakeClock,
) -> tuple[DeterministicIdGenerator, str, str]:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    ids = _ids()
    user_id = bootstrap_identity(database, settings, clock, ids).user_id
    file_id = str(ids.new_id())
    with transaction(database) as session:
        session.add(
            UserFile(
                id=file_id,
                user_id=user_id,
                provider_file_id="provider-file",
                filename="jd.txt",
                content_type="text/plain",
                size_bytes=20,
                storage_path=str(settings.data_dir / "uploads" / "jd.txt"),
                state="STORED",
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
    action = propose_add(
        KnowledgeServices(database, clock, ids),
        user_id,
        "tool-call-add-routing",
        AddKnowledgeArguments(
            source_file_id=file_id,
            title="Backend Engineer JD",
            document_type=DocumentType.JOB_REQUIREMENT,
            content="# Requirements\nPython distributed systems and database design",
            company="Example Corp",
            position="Backend Engineer",
            tags=("python", "backend"),
        ),
    )
    resolved = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {action.confirmation_code}",
        clock=clock,
        ids=ids,
    )
    assert resolved is not None
    result = execute_add(KnowledgeServices(database, clock, ids), action.id)
    assert result.document_id is not None
    return ids, user_id, result.document_id


def test_add_knowledge_action_creates_confirmation_without_mail_association(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    ids, user_id, _ = _create_document(database, settings, fake_clock)
    mail_services = MailServices(database, fake_clock, ids)

    publish_once(EventServices(mail_services, FakeLLM()))

    with transaction(database) as session:
        notifications = tuple(session.scalars(select(Notification)))
        processed = tuple(session.scalars(select(ProcessedEvent)))
        assert len(notifications) == 1
        assert notifications[0].user_id == user_id
        assert notifications[0].type == "PENDING_ACTION_CONFIRMATION"
        assert notifications[0].related_mail_id is None
        assert notifications[0].related_pending_action_id is not None
        assert len(processed) == 1


def test_remove_knowledge_action_creates_confirmation_and_duplicate_is_idempotent(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    ids, user_id, document_id = _create_document(database, settings, fake_clock)
    services = KnowledgeServices(database, fake_clock, ids)
    action = propose_remove(
        services,
        user_id,
        "tool-call-remove-routing",
        RemoveKnowledgeArguments(document_id=document_id),
    )
    with transaction(database) as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "PENDING_ACTION_CREATED",
                OutboxEvent.aggregate_id == action.id,
            )
        )
        assert event is not None
        event_id = event.id

    mail_services = MailServices(database, fake_clock, ids)
    consume_action_created(EventServices(mail_services, FakeLLM()), event_id, action.id)
    consume_action_created(EventServices(mail_services, FakeLLM()), event_id, action.id)

    with transaction(database) as session:
        notifications = tuple(session.scalars(select(Notification)))
        processed = tuple(session.scalars(select(ProcessedEvent)))
        assert len(notifications) == 1
        assert notifications[0].user_id == user_id
        assert notifications[0].related_mail_id is None
        assert notifications[0].related_pending_action_id == action.id
        assert len(processed) == 1


def test_mismatched_knowledge_action_rolls_back_and_reports_diagnostic(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    ids, _, _ = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        action = session.scalars(select(PendingAction)).first()
        assert action is not None
        action.source_type = "mail_analysis"
        action.mail_analysis_id = None
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "PENDING_ACTION_CREATED",
                OutboxEvent.aggregate_id == action.id,
            )
        )
        assert event is not None
        event_id = event.id

    mail_services = MailServices(database, fake_clock, ids)
    publish_once(EventServices(mail_services, FakeLLM()))

    with transaction(database) as session:
        assert session.scalar(select(Notification.id)) is None
        assert session.scalar(select(ProcessedEvent.id)) is None
        event = session.get(OutboxEvent, event_id)
        assert event is not None
        assert event.status == "PENDING"
        assert event.last_error is not None
        assert "mismatched source" in event.last_error


def test_unsupported_action_type_rolls_back_and_reports_diagnostic(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    ids, _, _ = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        action = session.scalars(select(PendingAction)).first()
        assert action is not None
        action.action_type = "UnsupportedAction"
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "PENDING_ACTION_CREATED",
                OutboxEvent.aggregate_id == action.id,
            )
        )
        assert event is not None
        event_id = event.id

    mail_services = MailServices(database, fake_clock, ids)
    publish_once(EventServices(mail_services, FakeLLM()))

    with transaction(database) as session:
        assert session.scalar(select(Notification.id)) is None
        assert session.scalar(select(ProcessedEvent.id)) is None
        event = session.get(OutboxEvent, event_id)
        assert event is not None
        assert event.status == "PENDING"
        assert event.last_error is not None
        assert "UnsupportedAction" in event.last_error
