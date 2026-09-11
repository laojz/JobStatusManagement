"""Atomic confirmed knowledge execution."""

import json

from sqlalchemy import select

from jobs_status_manager.application_core.domain import EventType, OutboxStatus, PendingActionState
from jobs_status_manager.application_core.models import OutboxEvent, PendingAction
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.knowledge.chunking import chunk_text
from jobs_status_manager.knowledge.contracts import (
    AddKnowledgeArguments,
    IndexStatus,
    KnowledgeLifecycle,
    RemoveKnowledgeArguments,
)
from jobs_status_manager.knowledge.dependencies import KnowledgeActionResult, KnowledgeServices
from jobs_status_manager.knowledge.models import KnowledgeChunk, KnowledgeDocument


def _outbox(services: KnowledgeServices, event_type: EventType, document_id: str) -> OutboxEvent:
    now = services.clock.now()
    event_id = str(services.ids.new_id())
    return OutboxEvent(
        id=event_id,
        event_id=event_id,
        event_type=event_type.value,
        event_version=1,
        aggregate_type="KnowledgeDocument",
        aggregate_id=document_id,
        producer="knowledge",
        payload={"document_id": document_id},
        occurred_at=now,
        status=OutboxStatus.PENDING.value,
        created_at=now,
        attempt_count=0,
    )


def execute_add(services: KnowledgeServices, action_id: str) -> KnowledgeActionResult:
    """Create document, chunks, outbox, and action completion atomically."""
    with transaction(services.database) as session:
        action = session.get(PendingAction, action_id)
        if action is None:
            raise LookupError(action_id)
        existing = session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.pending_action_id == action.id)
        )
        if existing is not None:
            return KnowledgeActionResult(
                action.id,
                PendingActionState.COMPLETED,
                existing.id,
                "added",
            )
        if action.state not in {
            PendingActionState.CONFIRMED.value,
            PendingActionState.FAILED.value,
        }:
            return KnowledgeActionResult(
                action.id, PendingActionState(action.state), None, "action is not executable"
            )
        arguments = AddKnowledgeArguments.model_validate(
            {
                **action.resolved_arguments,
                "tags": json.loads(str(action.resolved_arguments["tags_json"])),
            }
        )
        chunks = chunk_text(arguments.content)
        if not chunks:
            message = "knowledge content produced no chunks"
            raise ValueError(message)
        now = services.clock.now()
        action.state = PendingActionState.EXECUTING.value
        action.execution_started_at = now
        action.attempt_count += 1
        document_id = str(services.ids.new_id())
        document = KnowledgeDocument(
            id=document_id,
            user_id=action.user_id,
            source_file_id=arguments.source_file_id,
            pending_action_id=action.id,
            title=arguments.title,
            document_type=arguments.document_type.value,
            content=arguments.content,
            company=arguments.company,
            department=arguments.department,
            position=arguments.position,
            knowledge_domain=arguments.knowledge_domain,
            tags=list(arguments.tags),
            chunk_count=len(chunks),
            embedding_model=arguments.embedding_model,
            embedding_version=arguments.embedding_version,
            lifecycle_status=KnowledgeLifecycle.ACTIVE.value,
            index_status=IndexStatus.PENDING.value,
            index_attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.flush()
        session.add_all(
            KnowledgeChunk(
                id=str(services.ids.new_id()),
                document_id=document_id,
                chunk_index=chunk.index,
                content=chunk.content,
                section_title=chunk.section_title,
                token_count=None,
                created_at=now,
                updated_at=now,
            )
            for chunk in chunks
        )
        session.add(_outbox(services, EventType.KNOWLEDGE_DOCUMENT_ADDED, document_id))
        action.state = PendingActionState.COMPLETED.value
        action.completed_at = now
        return KnowledgeActionResult(action.id, PendingActionState.COMPLETED, document_id, "added")


def execute_remove(services: KnowledgeServices, action_id: str) -> KnowledgeActionResult:
    """Make a document immediately ineligible for retrieval."""
    with transaction(services.database) as session:
        action = session.get(PendingAction, action_id)
        if action is None:
            raise LookupError(action_id)
        arguments = RemoveKnowledgeArguments.model_validate(action.resolved_arguments)
        document = session.get(KnowledgeDocument, arguments.document_id)
        if action.state == PendingActionState.COMPLETED.value:
            return KnowledgeActionResult(
                action.id, PendingActionState.COMPLETED, arguments.document_id, "removed"
            )
        if action.state not in {
            PendingActionState.CONFIRMED.value,
            PendingActionState.FAILED.value,
        }:
            return KnowledgeActionResult(
                action.id, PendingActionState(action.state), None, "action is not executable"
            )
        if document is None or document.user_id != action.user_id:
            raise LookupError(arguments.document_id)
        now = services.clock.now()
        action.state = PendingActionState.EXECUTING.value
        action.execution_started_at = now
        action.attempt_count += 1
        if document.lifecycle_status == KnowledgeLifecycle.ACTIVE.value:
            document.lifecycle_status = KnowledgeLifecycle.REMOVED.value
            document.updated_at = now
            session.add(_outbox(services, EventType.KNOWLEDGE_DOCUMENT_REMOVED, document.id))
        action.state = PendingActionState.COMPLETED.value
        action.completed_at = now
        return KnowledgeActionResult(
            action.id,
            PendingActionState.COMPLETED,
            document.id,
            "removed",
        )
