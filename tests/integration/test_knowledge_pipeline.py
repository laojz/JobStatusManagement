"""Phase 5 relational and vector knowledge integration tests."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, text

from jobs_status_manager.application.lifecycle import LifecycleAdapters, _knowledge_cycle
from jobs_status_manager.application.operations import TaskKind, retry_task
from jobs_status_manager.application_core.domain import PendingActionState, UserId
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.application_core.service import resolve_confirmation
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.chroma import LocalChroma
from jobs_status_manager.infrastructure.adapters.fakes import FakeChroma, FakeEmbedding
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.contracts import (
    AddKnowledgeArguments,
    DocumentType,
    IndexStatus,
    KnowledgeLifecycle,
    RemoveKnowledgeArguments,
    SearchKnowledgeInput,
    VectorHit,
)
from jobs_status_manager.knowledge.index import (
    IndexRetryServices,
    IndexServices,
    cleanup_removed_once,
    index_once,
    recover_stale_indexing,
    search,
)
from jobs_status_manager.knowledge.models import KnowledgeChunk, KnowledgeDocument, UserFile
from jobs_status_manager.knowledge.rebuild import rebuild_index
from jobs_status_manager.knowledge.service import (
    KnowledgeServices,
    execute_add,
    execute_remove,
    propose_add,
    propose_remove,
)
from jobs_status_manager.mail import MAX_ERROR_CHARS


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
    services = KnowledgeServices(database, clock, ids)
    action = propose_add(
        services,
        user_id,
        "tool-call-add",
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
    result = execute_add(services, action.id)
    assert result.document_id is not None
    return ids, user_id, result.document_id


def test_confirmed_document_is_indexed_searched_and_removed(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    ids, user_id, document_id = _create_document(database, settings, fake_clock)
    embedding = FakeEmbedding(vector=[0.2, 0.4])
    chroma = FakeChroma()
    adapters = LifecycleAdapters(clock=fake_clock, ids=ids, embedding=embedding, chroma=chroma)

    _knowledge_cycle(database, adapters)

    with database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT index_status FROM knowledge_documents WHERE id = :id"),
                {"id": document_id},
            ).scalar_one()
            == IndexStatus.READY.value
        )
        chunk_id = connection.execute(
            text("SELECT id FROM knowledge_chunks WHERE document_id = :id"),
            {"id": document_id},
        ).scalar_one()
    assert tuple(chroma.records) == (chunk_id,)
    chroma.hits.append(VectorHit(chunk_id, 0.1))

    output = search(
        IndexServices(database, fake_clock, embedding, chroma),
        user_id,
        SearchKnowledgeInput(query="distributed systems"),
    )

    assert tuple(result.document_id for result in output.results) == (document_id,)
    remove = propose_remove(
        KnowledgeServices(database, fake_clock, ids),
        user_id,
        "tool-call-remove",
        RemoveKnowledgeArguments(document_id=document_id),
    )
    resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {remove.confirmation_code}",
        clock=fake_clock,
        ids=ids,
    )
    execute_remove(KnowledgeServices(database, fake_clock, ids), remove.id)
    assert not search(
        IndexServices(database, fake_clock, embedding, chroma),
        user_id,
        SearchKnowledgeInput(query="distributed systems"),
    ).results

    _knowledge_cycle(database, adapters)

    assert not chroma.records


def test_rebuild_and_search_with_real_local_chroma(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, user_id, document_id = _create_document(database, settings, fake_clock)
    embedding = FakeEmbedding(vector=[0.2, 0.4])
    chroma = LocalChroma(settings.data_dir / "chroma")
    services = IndexServices(database, fake_clock, embedding, chroma)

    summary = rebuild_index(services)

    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        chunks = tuple(
            session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
        )
        assert document.lifecycle_status == KnowledgeLifecycle.ACTIVE.value
        assert document.index_status == IndexStatus.READY.value
    assert summary.total_documents == 1
    assert summary.ready_documents == 1
    assert summary.failed_documents == 0
    assert summary.total_chunks == len(chunks)
    assert summary.upserted_chunks == len(chunks)
    assert summary.partial_failure is False

    output = search(
        services,
        user_id,
        SearchKnowledgeInput(query="Python distributed systems"),
    )

    assert output.results
    assert {result.document_id for result in output.results} == {document_id}
    for result in output.results:
        assert 0.0 <= result.score <= 1.0
        assert result.metadata.document_id == document_id
        assert result.metadata.document_type == DocumentType.JOB_REQUIREMENT.value
        assert result.metadata.company == "Example Corp"


def test_embedding_failure_preserves_document_and_marks_index_failed(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    embedding = FakeEmbedding(error=RuntimeError("embedding unavailable"))

    result = index_once(IndexServices(database, fake_clock, embedding, FakeChroma()))

    assert result == document_id
    with transaction(database) as session:
        document = session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        assert document is not None
        chunks = tuple(
            session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
        )
        action = session.scalar(
            select(PendingAction).where(PendingAction.id == document.pending_action_id)
        )
        assert action is not None
        assert document.index_status == IndexStatus.FAILED.value
        assert document.lifecycle_status == KnowledgeLifecycle.ACTIVE.value
        assert document.last_index_error == safe_external_error(
            RuntimeError("embedding unavailable")
        )
        assert chunks
        assert action.state == PendingActionState.COMPLETED.value


def test_index_failure_persists_bounded_error(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    error_message = "x" * (MAX_ERROR_CHARS + 50)

    result = index_once(
        IndexServices(
            database,
            fake_clock,
            FakeEmbedding(error=RuntimeError(error_message)),
            FakeChroma(),
        )
    )

    assert result == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.last_index_error == safe_external_error(RuntimeError(error_message))


def test_removed_cleanup_failure_persists_bounded_error(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    assert (
        index_once(
            IndexServices(
                database,
                fake_clock,
                FakeEmbedding(vector=[0.2, 0.4]),
                FakeChroma(),
            )
        )
        == document_id
    )
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.lifecycle_status = KnowledgeLifecycle.REMOVED.value
    error_message = "x" * (MAX_ERROR_CHARS + 50)

    result = cleanup_removed_once(
        IndexServices(
            database,
            fake_clock,
            FakeEmbedding(vector=[0.2, 0.4]),
            FakeChroma(error=RuntimeError(error_message)),
        )
    )

    assert result == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.last_index_error == safe_external_error(RuntimeError(error_message))


def test_rebuild_failure_persists_bounded_error(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    error_message = "x" * (MAX_ERROR_CHARS + 50)

    summary = rebuild_index(
        IndexServices(
            database,
            fake_clock,
            FakeEmbedding(error=RuntimeError(error_message)),
            FakeChroma(),
        )
    )

    assert summary.partial_failure is True
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.last_index_error == safe_external_error(RuntimeError(error_message))


def test_automatic_indexing_stops_retrying_after_attempt_ceiling(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    embedding = FakeEmbedding(error=RuntimeError("embedding unavailable"))
    services = IndexServices(database, fake_clock, embedding, FakeChroma())

    for _ in range(3):
        assert index_once(services) == document_id

    assert index_once(services) is None
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_attempt_count == 3
        assert document.last_index_error == safe_external_error(
            RuntimeError("embedding unavailable")
        )


def test_stale_indexing_document_is_reclaimed_and_can_finish(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.index_status = IndexStatus.INDEXING.value
        document.index_started_at = fake_clock.now()
    fake_clock.advance(301)

    embedding = FakeEmbedding(vector=[0.2, 0.4])
    chroma = FakeChroma()
    assert index_once(IndexServices(database, fake_clock, embedding, chroma)) == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_status == IndexStatus.READY.value


def test_stale_indexing_at_attempt_ceiling_becomes_failed(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.index_status = IndexStatus.INDEXING.value
        document.index_attempt_count = 3
        document.index_started_at = fake_clock.now()
    fake_clock.advance(301)

    assert recover_stale_indexing(IndexRetryServices(database, fake_clock)) == 1
    assert index_once(IndexServices(database, fake_clock, FakeEmbedding(), FakeChroma())) is None
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_status == IndexStatus.FAILED.value
        assert document.index_attempt_count == 3
        assert document.last_index_error == "automatic indexing attempts exhausted"


def test_repeated_stale_recovery_cannot_bypass_attempt_ceiling(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.index_status = IndexStatus.INDEXING.value
        document.index_attempt_count = 2
        document.index_started_at = fake_clock.now()
    fake_clock.advance(301)
    services = IndexServices(
        database, fake_clock, FakeEmbedding(error=RuntimeError("failed")), FakeChroma()
    )

    assert recover_stale_indexing(IndexRetryServices(database, fake_clock)) == 1
    assert index_once(services) == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.index_status = IndexStatus.INDEXING.value
        document.index_started_at = fake_clock.now()
    fake_clock.advance(301)
    assert recover_stale_indexing(IndexRetryServices(database, fake_clock)) == 1
    assert index_once(services) is None
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_status == IndexStatus.FAILED.value
        assert document.index_attempt_count == 3


def test_rebuild_reset_failure_keeps_documents_ineligible(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, document_id = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.index_status = IndexStatus.READY.value

    class ResetFailingChroma(FakeChroma):
        def reset(self) -> None:
            message = "reset failed"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="reset failed"):
        rebuild_index(
            IndexServices(
                database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), ResetFailingChroma()
            )
        )

    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_status == IndexStatus.PENDING.value


def test_rebuild_partial_failure_marks_only_successful_documents_ready(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, _, first_id = _create_document(database, settings, fake_clock)
    with transaction(database) as session:
        first = session.get(KnowledgeDocument, first_id)
        assert first is not None
        first.title = "First"
        first.index_status = IndexStatus.READY.value
    summary = rebuild_index(
        IndexServices(
            database,
            fake_clock,
            FakeEmbedding(error=RuntimeError("partial rebuild")),
            FakeChroma(),
        )
    )

    assert summary.failed_documents == 1
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, first_id)
        assert document is not None
        assert document.index_status == IndexStatus.FAILED.value


def test_cleanup_failure_remains_observable_and_manual_retryable(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, user_id, document_id = _create_document(database, settings, fake_clock)
    initial_chroma = FakeChroma()
    initial_services = IndexServices(
        database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), initial_chroma
    )
    assert index_once(initial_services) == document_id

    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.lifecycle_status = KnowledgeLifecycle.REMOVED.value

    failing_chroma = FakeChroma(error=RuntimeError("chroma unavailable"))
    failing_services = IndexServices(
        database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), failing_chroma
    )
    assert cleanup_removed_once(failing_services) == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        assert document.index_status == IndexStatus.FAILED.value
        assert document.last_index_error == safe_external_error(RuntimeError("chroma unavailable"))

    retry_chroma = FakeChroma()
    retry_services = IndexServices(
        database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), retry_chroma
    )
    retry_result = retry_task(database, TaskKind.KNOWLEDGE_CLEANUP, document_id, fake_clock)
    assert retry_result.state == "RETRY_WAIT"
    assert retry_result.message == (
        "knowledge cleanup requeued; remains FAILED until deletion succeeds"
    )
    assert cleanup_removed_once(retry_services) == document_id
    assert not retry_chroma.records
    assert not search(
        retry_services,
        user_id,
        SearchKnowledgeInput(query="distributed systems"),
    ).results


def test_rebuild_resets_collection_excludes_removed_and_is_repeatable(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _, user_id, document_id = _create_document(database, settings, fake_clock)
    embedding = FakeEmbedding(vector=[0.2, 0.4])
    chroma = FakeChroma()
    services = IndexServices(database, fake_clock, embedding, chroma)
    assert index_once(services) == document_id
    with transaction(database) as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document is not None
        document.lifecycle_status = KnowledgeLifecycle.REMOVED.value

    first = rebuild_index(services)
    second = rebuild_index(services)

    assert first.total_documents == 0
    assert first.ready_documents == 0
    assert first.failed_documents == 0
    assert first.partial_failure is False
    assert second == first
    assert not chroma.records
    assert not search(services, user_id, SearchKnowledgeInput(query="distributed systems")).results
