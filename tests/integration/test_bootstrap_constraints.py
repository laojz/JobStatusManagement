"""Identity constraint tests."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from jobs_status_manager.application.lifecycle import LifecycleAdapters, _startup_recovery
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.bootstrap.service import BootstrapConflictError, bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeChroma, FakeEmbedding
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.knowledge.contracts import IndexStatus, KnowledgeLifecycle
from jobs_status_manager.knowledge.models import KnowledgeDocument
from jobs_status_manager.notification_models import Notification


def test_bootstrap_conflict_is_explicit(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    ids: DeterministicIdGenerator,
) -> None:
    """Changing an existing identity is not silently overwritten."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    bootstrap_identity(database, settings, fake_clock, ids)
    conflicting = settings.model_copy(update={"bootstrap_user_display_name": "Other User"})
    with pytest.raises(BootstrapConflictError, match=r"user\.display_name"):
        bootstrap_identity(database, conflicting, fake_clock, ids)


def test_foreign_key_and_unique_constraints_are_enforced(
    database: Database, settings: AppSettings
) -> None:
    """Database constraints reject orphan and duplicate account records."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    with (
        database.engine.begin() as connection,
        pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"),
    ):
        connection.execute(
            text(
                "INSERT INTO mail_accounts (id, user_id, provider, account_key, display_name, "
                "is_active, created_at, updated_at) VALUES "
                "('a', 'missing', 'local', 'key', 'name', 1, '2026-01-01', '2026-01-01')"
            )
        )


def test_startup_recovery_scans_all_durable_worker_states(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    """Startup clears stale durable leases before worker cycles can process them."""
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    ids = DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 20)]
    )
    bootstrap_identity(database, settings, fake_clock, ids)
    with database.engine.connect() as connection:
        user_id = connection.execute(text("SELECT id FROM users")).scalar_one()
    stale = fake_clock.now() - timedelta(minutes=10)
    with transaction(database) as session:
        session.add(
            Notification(
                id="notification-1",
                user_id=user_id,
                type="JOB_MAIL",
                channel="QQ_PUSH",
                title="title",
                content="content",
                source_event_id="event-1",
                state="SENDING",
                attempt_count=1,
                last_attempt_at=stale,
                created_at=stale,
                updated_at=stale,
            )
        )
        session.add(
            PendingAction(
                id="pending-1",
                user_id=user_id,
                source_type="test",
                source_id="source-1",
                action_type="AddKnowledge",
                resolved_arguments={},
                display_summary="test",
                proposal_fingerprint="fingerprint-1",
                confirmation_code="PA-TEST1",
                state="COMPLETED",
                created_at=stale,
                expires_at=fake_clock.now() + timedelta(days=1),
            )
        )
        session.add(
            KnowledgeDocument(
                id="document-1",
                user_id=user_id,
                pending_action_id="pending-1",
                title="title",
                document_type="JOB_REQUIREMENT",
                content="content",
                tags=[],
                chunk_count=0,
                embedding_model="test",
                embedding_version="1",
                lifecycle_status=KnowledgeLifecycle.ACTIVE.value,
                index_status=IndexStatus.INDEXING.value,
                index_attempt_count=1,
                index_started_at=stale,
                created_at=stale,
                updated_at=stale,
            )
        )

    _startup_recovery(
        database,
        LifecycleAdapters(
            clock=fake_clock,
            ids=ids,
            embedding=FakeEmbedding(vector=[0.1]),
            chroma=FakeChroma(),
        ),
        settings,
    )

    with database.engine.connect() as connection:
        notification = connection.execute(
            text("SELECT state FROM notifications WHERE id='notification-1'")
        ).scalar_one()
        index_status = connection.execute(
            text("SELECT index_status FROM knowledge_documents WHERE id='document-1'")
        ).scalar_one()
    assert notification == "RETRY_WAIT"
    assert index_status == "PENDING"
