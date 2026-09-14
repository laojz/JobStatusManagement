"""REQ-OPS01 local, non-destructive recovery drill."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from jobs_status_manager.application.operations import (
    backup_database,
    check_integrity,
    restore_check,
)
from jobs_status_manager.application_core.domain import UserId
from jobs_status_manager.application_core.models import Application
from jobs_status_manager.application_core.service import resolve_confirmation
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.chroma import LocalChroma
from jobs_status_manager.infrastructure.adapters.fakes import FakeEmbedding
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import (
    current_revision,
    upgrade_database,
)
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.knowledge.contracts import (
    AddKnowledgeArguments,
    DocumentType,
    SearchKnowledgeInput,
)
from jobs_status_manager.knowledge.index import IndexServices, search
from jobs_status_manager.knowledge.models import UserFile
from jobs_status_manager.knowledge.rebuild import rebuild_index
from jobs_status_manager.knowledge.service import KnowledgeServices, execute_add, propose_add


def _create_document(
    database: Database,
    settings: AppSettings,
    clock: FakeClock,
) -> tuple[DeterministicIdGenerator, str, str]:
    """Create the existing representative upload and knowledge rows."""
    upgrade_database(Path(__file__).resolve().parents[2], f"sqlite:///{settings.database_path}")
    ids = DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 100)]
    )
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
                size_bytes=38,
                storage_path=str(settings.data_dir / "uploads" / "jd.txt"),
                state="STORED",
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
    action = propose_add(
        KnowledgeServices(database, clock, ids),
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
    assert (
        resolve_confirmation(
            database,
            user_id=UserId(user_id),
            command_text=f"确认 {action.confirmation_code}",
            clock=clock,
            ids=ids,
        )
        is not None
    )
    result = execute_add(KnowledgeServices(database, clock, ids), action.id)
    assert result.document_id is not None
    return ids, user_id, result.document_id


def _counts(database_path: Path) -> dict[str, int]:
    """Read representative business, upload, and knowledge row counts."""
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            "applications": int(
                connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            ),
            "user_files": int(connection.execute("SELECT COUNT(*) FROM user_files").fetchone()[0]),
            "knowledge_documents": int(
                connection.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0]
            ),
            "knowledge_chunks": int(
                connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
            ),
        }


def test_local_recovery_drill_restores_isolated_wal_state_and_rebuilds_index(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    root = Path(__file__).resolve().parents[2]
    drill_root = settings.data_dir.parent / "recovery-drill"
    upload_path = settings.data_dir / "uploads" / "jd.txt"
    restored_database = None
    try:
        ids, user_id, document_id = _create_document(database, settings, fake_clock)
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        upload_path.write_bytes(b"Backend Engineer JD\nPython and SQLite")
        with transaction(database) as session:
            session.add(
                Application(
                    id=str(ids.new_id()),
                    user_id=user_id,
                    company="Example Corp",
                    department="Platform",
                    position="Backend Engineer",
                    company_key="example-corp",
                    department_key="platform",
                    position_key="backend-engineer",
                    current_status="APPLIED",
                    created_at=fake_clock.now(),
                    updated_at=fake_clock.now(),
                )
            )
        assert database.pragmas()["journal_mode"] == "wal"
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE applications SET current_status = :status"),
                {"status": "INTERVIEWING"},
            )
        source_counts = _counts(database.path)
        assert source_counts == {
            "applications": 1,
            "user_files": 1,
            "knowledge_documents": 1,
            "knowledge_chunks": 1,
        }

        backup_path = drill_root / "backup" / "jobs.db"
        backup = backup_database(database, backup_path, root)
        assert backup.destination != database.path.resolve()
        assert restore_check(backup.destination, root).ok
        assert check_integrity(backup.destination, root).ok

        restored_upload = drill_root / "restored" / "uploads" / upload_path.name
        restored_upload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(upload_path, restored_upload)
        restored_database = Database(drill_root / "restored" / "jobs.db")
        shutil.copy2(backup.destination, restored_database.path)
        assert check_integrity(restored_database.path, root).ok
        assert _counts(restored_database.path) == source_counts
        assert restored_upload.read_bytes() == upload_path.read_bytes()
        assert (
            current_revision(f"sqlite:///{restored_database.path}")
            == "0009_tool_call_provider_metadata"
        )

        chroma = LocalChroma(drill_root / "restored" / "chroma")
        summary = rebuild_index(
            IndexServices(restored_database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), chroma)
        )
        assert summary.total_documents == 1
        assert summary.ready_documents == 1
        assert summary.failed_documents == 0
        assert summary.total_chunks == summary.upserted_chunks
        assert summary.partial_failure is False
        results = search(
            IndexServices(restored_database, fake_clock, FakeEmbedding(vector=[0.2, 0.4]), chroma),
            user_id,
            SearchKnowledgeInput(query="Python SQLite"),
        )
        assert {result.document_id for result in results.results} == {document_id}
    finally:
        if restored_database is not None:
            restored_database.dispose()
        shutil.rmtree(drill_root, ignore_errors=True)
        assert not drill_root.exists()
        upload_path.unlink(missing_ok=True)
        assert not upload_path.exists()
