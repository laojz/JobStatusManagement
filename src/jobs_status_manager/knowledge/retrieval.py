"""Relationally validated vector retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.knowledge.contracts import (
    DocumentType,
    KnowledgeMetadata,
    SearchKnowledgeInput,
    SearchKnowledgeOutput,
    SearchKnowledgeResult,
)
from jobs_status_manager.knowledge.models import KnowledgeChunk, KnowledgeDocument

if TYPE_CHECKING:
    from jobs_status_manager.knowledge.index import IndexServices


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


def _eligible(session: Session, user_id: str, request: SearchKnowledgeInput) -> tuple[str, ...]:
    statement = select(KnowledgeDocument.id).where(
        KnowledgeDocument.user_id == user_id,
        KnowledgeDocument.lifecycle_status == "ACTIVE",
        KnowledgeDocument.index_status == "READY",
    )
    filters = request.filters
    for column, value in (
        (
            KnowledgeDocument.document_type,
            filters.document_type.value if filters.document_type else None,
        ),
        (KnowledgeDocument.company, filters.company),
        (KnowledgeDocument.department, filters.department),
        (KnowledgeDocument.position, filters.position),
        (KnowledgeDocument.knowledge_domain, filters.knowledge_domain),
    ):
        if value is not None:
            statement = statement.where(column == value)
    return tuple(session.scalars(statement.limit(request.max_documents)))


def search(
    services: IndexServices,
    user_id: str,
    request: SearchKnowledgeInput,
) -> SearchKnowledgeOutput:
    """Retrieve and relationally validate vector hits."""
    with Session(services.database.engine) as session:
        document_ids = _eligible(session, user_id, request)
    if not document_ids:
        return SearchKnowledgeOutput(results=())
    embedding = tuple(services.embedding.embed(request.query))
    hits = services.chroma.query(embedding, document_ids, request.top_k)
    results: list[SearchKnowledgeResult] = []
    with Session(services.database.engine) as session:
        for hit in hits:
            chunk = session.get(KnowledgeChunk, hit.record_id)
            if chunk is None or chunk.document_id not in document_ids:
                continue
            document = session.get(KnowledgeDocument, chunk.document_id)
            if document is None:
                continue
            results.append(
                SearchKnowledgeResult(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    document_type=DocumentType(document.document_type),
                    content=chunk.content,
                    section_title=chunk.section_title,
                    score=max(0.0, min(1.0, 1.0 - hit.distance)),
                    metadata=_metadata(document, chunk),
                )
            )
    return SearchKnowledgeOutput(results=tuple(results))
