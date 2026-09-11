"""Vector indexing workers and retrieval service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.contracts import (
    IndexStatus,
    KnowledgeLifecycle,
    KnowledgeMetadata,
    VectorRecord,
)
from jobs_status_manager.knowledge.models import KnowledgeChunk, KnowledgeDocument
from jobs_status_manager.knowledge.retrieval import search

__all__ = [
    "IndexRetryServices",
    "IndexServices",
    "cleanup_removed_once",
    "index_once",
    "recover_stale_indexing",
    "retry_cleanup",
    "retry_index",
    "search",
]

if TYPE_CHECKING:
    from jobs_status_manager.infrastructure.adapters.protocols import (
        ChromaAdapter,
        EmbeddingAdapter,
    )
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database


@dataclass(frozen=True, slots=True)
class IndexServices:
    """Dependencies for external vector-index work."""

    database: Database
    clock: Clock
    embedding: EmbeddingAdapter
    chroma: ChromaAdapter


@dataclass(frozen=True, slots=True)
class IndexRetryServices:
    """Dependencies for relational retry state transitions."""

    database: Database
    clock: Clock


MAX_AUTOMATIC_ATTEMPTS: Final = 3


def _metadata(document: KnowledgeDocument, chunk: KnowledgeChunk) -> KnowledgeMetadata:
    return KnowledgeMetadata(
        user_id=document.user_id,
        document_id=document.id,
        document_type=document.document_type,
        company=document.company or "",
        department=document.department or "",
        position=document.position or "",
        knowledge_domain=document.knowledge_domain or "",
        section_title=chunk.section_title or "",
        chunk_index=chunk.chunk_index,
        embedding_version=document.embedding_version,
    )


def index_once(
    services: IndexServices,
    stale_after: timedelta = timedelta(minutes=5),
) -> str | None:
    """Claim and index one persisted document outside its DB transactions."""
    now = services.clock.now()
    with transaction(services.database) as session:
        document = session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.lifecycle_status == KnowledgeLifecycle.ACTIVE.value,
                (KnowledgeDocument.index_status == IndexStatus.PENDING.value)
                | (
                    (KnowledgeDocument.index_status == IndexStatus.FAILED.value)
                    & (KnowledgeDocument.index_attempt_count < MAX_AUTOMATIC_ATTEMPTS)
                )
                | (
                    (KnowledgeDocument.index_status == IndexStatus.INDEXING.value)
                    & (KnowledgeDocument.index_started_at < now - stale_after)
                ),
            )
            .order_by(KnowledgeDocument.created_at)
        )
        if document is None:
            return None
        if (
            document.index_status == IndexStatus.INDEXING.value
            and document.index_attempt_count >= MAX_AUTOMATIC_ATTEMPTS
        ):
            document.index_status = IndexStatus.FAILED.value
            document.last_index_error = "automatic indexing attempts exhausted"
            document.updated_at = now
            return document.id
        document.index_status = IndexStatus.INDEXING.value
        document.index_started_at = now
        document.index_attempt_count += 1
        document.last_index_error = None
        document_id = document.id
    with Session(services.database.engine) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return None
        chunks = tuple(
            session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
        inputs = tuple((chunk.id, chunk.content, _metadata(document, chunk)) for chunk in chunks)
    try:
        records = tuple(
            VectorRecord(
                chunk_id,
                content,
                tuple(services.embedding.embed(content)),
                metadata,
            )
            for chunk_id, content, metadata in inputs
        )
        services.chroma.upsert(records)
    except (RuntimeError, ValueError, TimeoutError) as error:
        with transaction(services.database) as session:
            failed = session.get(KnowledgeDocument, document_id)
            if failed is not None:
                failed.index_status = IndexStatus.FAILED.value
                failed.last_index_error = safe_external_error(error)
                failed.updated_at = services.clock.now()
        return document_id
    with transaction(services.database) as session:
        ready = session.get(KnowledgeDocument, document_id)
        if ready is not None and ready.lifecycle_status == KnowledgeLifecycle.ACTIVE.value:
            ready.index_status = IndexStatus.READY.value
            ready.last_indexed_at = services.clock.now()
            ready.updated_at = services.clock.now()
    return document_id


def recover_stale_indexing(
    services: IndexRetryServices, stale_after: timedelta = timedelta(minutes=5)
) -> int:
    """Return stale active indexing leases to the durable pending state."""
    now = services.clock.now()
    with transaction(services.database) as session:
        documents = session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.lifecycle_status == KnowledgeLifecycle.ACTIVE.value,
                KnowledgeDocument.index_status == IndexStatus.INDEXING.value,
                KnowledgeDocument.index_started_at < now - stale_after,
            )
        )
        recovered = 0
        for document in documents:
            if document.index_attempt_count >= MAX_AUTOMATIC_ATTEMPTS:
                document.index_status = IndexStatus.FAILED.value
                document.last_index_error = "automatic indexing attempts exhausted"
            else:
                document.index_status = IndexStatus.PENDING.value
            document.updated_at = now
            recovered += 1
        return recovered


def cleanup_removed_once(services: IndexServices) -> str | None:
    """Delete one removed document's stable Chroma records."""
    with Session(services.database.engine) as session:
        document = session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.lifecycle_status == KnowledgeLifecycle.REMOVED.value,
                KnowledgeDocument.index_status != IndexStatus.PENDING.value,
                KnowledgeDocument.index_attempt_count < MAX_AUTOMATIC_ATTEMPTS,
            )
            .order_by(KnowledgeDocument.updated_at)
        )
        if document is None:
            return None
        ids = tuple(
            session.scalars(
                select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document.id)
            )
        )
        document_id = document.id
    try:
        services.chroma.delete(ids)
    except (RuntimeError, ValueError, TimeoutError) as error:
        with transaction(services.database) as session:
            removed = session.get(KnowledgeDocument, document_id)
            if removed is not None:
                removed.index_status = IndexStatus.FAILED.value
                removed.index_attempt_count += 1
                removed.last_index_error = safe_external_error(error)
                removed.updated_at = services.clock.now()
        return document_id
    with transaction(services.database) as session:
        removed = session.get(KnowledgeDocument, document_id)
        if removed is not None:
            removed.index_status = IndexStatus.PENDING.value
            removed.updated_at = services.clock.now()
    return document_id


def retry_index(services: IndexRetryServices | IndexServices, document_id: str) -> bool:
    """Explicitly requeue an existing failed or stale index operation."""
    with transaction(services.database) as session:
        document = session.get(KnowledgeDocument, document_id)
        if (
            document is None
            or document.lifecycle_status != KnowledgeLifecycle.ACTIVE.value
            or document.index_status != IndexStatus.FAILED.value
        ):
            return False
        document.index_attempt_count = 0
        document.updated_at = services.clock.now()
    return True


def retry_cleanup(services: IndexRetryServices | IndexServices, document_id: str) -> bool:
    """Explicitly requeue cleanup for an existing removed document."""
    with transaction(services.database) as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None or document.lifecycle_status != KnowledgeLifecycle.REMOVED.value:
            return False
        if document.index_status != IndexStatus.FAILED.value:
            return False
        document.index_attempt_count = 0
        document.updated_at = services.clock.now()
    return True
