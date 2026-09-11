"""Full relational-to-vector rebuild operations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.knowledge.contracts import IndexStatus, KnowledgeLifecycle, VectorRecord
from jobs_status_manager.knowledge.index import IndexServices, _metadata
from jobs_status_manager.knowledge.models import KnowledgeChunk, KnowledgeDocument


@dataclass(frozen=True, slots=True)
class RebuildSummary:
    """Typed result of a full relational-to-vector rebuild."""

    total_documents: int
    ready_documents: int
    failed_documents: int
    total_chunks: int
    upserted_chunks: int
    partial_failure: bool


def _active_documents(
    services: IndexServices,
) -> tuple[tuple[KnowledgeDocument, tuple[KnowledgeChunk, ...]], ...]:
    with Session(services.database.engine) as session:
        documents = tuple(
            session.scalars(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.lifecycle_status == KnowledgeLifecycle.ACTIVE.value
                )
            )
        )
        return tuple(
            (
                document,
                tuple(
                    session.scalars(
                        select(KnowledgeChunk)
                        .where(KnowledgeChunk.document_id == document.id)
                        .order_by(KnowledgeChunk.chunk_index)
                    )
                ),
            )
            for document in documents
        )


def rebuild_index(services: IndexServices) -> RebuildSummary:
    """Reset Chroma and rebuild it from ACTIVE relational documents."""
    documents = _active_documents(services)
    now = services.clock.now()
    with transaction(services.database) as session:
        for document, _ in documents:
            active = session.get(KnowledgeDocument, document.id)
            if active is not None:
                active.index_status = IndexStatus.PENDING.value
                active.last_index_error = None
                active.updated_at = now
    try:
        services.chroma.reset()
    except (RuntimeError, ValueError, TimeoutError) as error:
        with transaction(services.database) as session:
            for document, _ in documents:
                active = session.get(KnowledgeDocument, document.id)
                if active is not None:
                    active.index_status = IndexStatus.PENDING.value
                    active.last_index_error = safe_external_error(error)
                    active.updated_at = services.clock.now()
        raise
    total_chunks = sum(len(chunks) for _, chunks in documents)
    ready_documents = 0
    failed_documents = 0
    upserted_chunks = 0
    for document, chunks in documents:
        records: tuple[VectorRecord, ...] = ()
        try:
            records = tuple(
                VectorRecord(
                    chunk.id,
                    chunk.content,
                    tuple(services.embedding.embed(chunk.content)),
                    _metadata(document, chunk),
                )
                for chunk in chunks
            )
            services.chroma.upsert(records)
        except (RuntimeError, ValueError, TimeoutError) as error:
            failed_documents += 1
            with transaction(services.database) as session:
                failed = session.get(KnowledgeDocument, document.id)
                if failed is not None:
                    failed.index_status = IndexStatus.FAILED.value
                    failed.last_index_error = safe_external_error(error)
                    failed.updated_at = services.clock.now()
            continue
        ready_documents += 1
        upserted_chunks += len(records)
        with transaction(services.database) as session:
            ready = session.get(KnowledgeDocument, document.id)
            if ready is not None:
                ready.index_status = IndexStatus.READY.value
                ready.last_index_error = None
                ready.last_indexed_at = services.clock.now()
                ready.updated_at = services.clock.now()
    return RebuildSummary(
        total_documents=len(documents),
        ready_documents=ready_documents,
        failed_documents=failed_documents,
        total_chunks=total_chunks,
        upserted_chunks=upserted_chunks,
        partial_failure=failed_documents > 0,
    )
