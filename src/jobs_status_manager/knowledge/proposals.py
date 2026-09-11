"""Frozen knowledge PendingAction creation."""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import select

from jobs_status_manager.application_core.domain import EventType, OutboxStatus, PendingActionState
from jobs_status_manager.application_core.models import OutboxEvent, PendingAction
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.knowledge.contracts import AddKnowledgeArguments, RemoveKnowledgeArguments
from jobs_status_manager.knowledge.dependencies import KnowledgeServices
from jobs_status_manager.knowledge.models import KnowledgeDocument


def _confirmation_code(action_id: str) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    value = int.from_bytes(hashlib.sha256(action_id.encode()).digest()[:8], "big")
    digits: list[str] = []
    for _ in range(4):
        value, remainder = divmod(value, len(alphabet))
        digits.append(alphabet[remainder])
    return "PA-" + "".join(reversed(digits))


def _fingerprint(action_type: str, payload: dict[str, str | int | None]) -> str:
    encoded = json.dumps(
        {"action_type": action_type, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _created_event(services: KnowledgeServices, action: PendingAction) -> OutboxEvent:
    now = services.clock.now()
    event_id = str(services.ids.new_id())
    return OutboxEvent(
        id=event_id,
        event_id=event_id,
        event_type=EventType.PENDING_ACTION_CREATED.value,
        event_version=1,
        aggregate_type="PendingAction",
        aggregate_id=action.id,
        producer="knowledge",
        payload={"confirmation_code": action.confirmation_code, "summary": action.display_summary},
        occurred_at=now,
        status=OutboxStatus.PENDING.value,
        created_at=now,
        attempt_count=0,
    )


def propose_add(
    services: KnowledgeServices,
    user_id: str,
    source_id: str,
    arguments: AddKnowledgeArguments,
) -> PendingAction:
    """Create or reuse one frozen AddKnowledge proposal."""
    payload: dict[str, str | int | None] = {
        "source_file_id": arguments.source_file_id,
        "title": arguments.title,
        "document_type": arguments.document_type.value,
        "content": arguments.content,
        "company": arguments.company,
        "department": arguments.department,
        "position": arguments.position,
        "knowledge_domain": arguments.knowledge_domain,
        "tags_json": json.dumps(arguments.tags, ensure_ascii=False),
        "embedding_model": arguments.embedding_model,
        "embedding_version": arguments.embedding_version,
    }
    fingerprint = _fingerprint("AddKnowledge", payload)
    with transaction(services.database) as session:
        existing = session.scalar(
            select(PendingAction).where(
                PendingAction.user_id == user_id,
                PendingAction.source_type == "agent_tool",
                PendingAction.source_id == source_id,
                PendingAction.action_type == "AddKnowledge",
                PendingAction.proposal_fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return existing
        now = services.clock.now()
        action_id = str(services.ids.new_id())
        action = PendingAction(
            id=action_id,
            user_id=user_id,
            source_type="agent_tool",
            source_id=source_id,
            action_type="AddKnowledge",
            resolved_arguments=payload,
            display_summary=f"添加知识: {arguments.title} ({arguments.document_type.value})",
            proposal_fingerprint=fingerprint,
            confirmation_code=_confirmation_code(action_id),
            state=PendingActionState.PENDING.value,
            created_at=now,
            expires_at=now + timedelta(days=7),
            attempt_count=0,
        )
        session.add(action)
        session.add(_created_event(services, action))
        return action


def propose_remove(
    services: KnowledgeServices,
    user_id: str,
    source_id: str,
    arguments: RemoveKnowledgeArguments,
) -> PendingAction:
    """Create or reuse one frozen RemoveKnowledge proposal."""
    payload: dict[str, str | int | None] = {"document_id": arguments.document_id}
    fingerprint = _fingerprint("RemoveKnowledge", payload)
    with transaction(services.database) as session:
        existing = session.scalar(
            select(PendingAction).where(
                PendingAction.user_id == user_id,
                PendingAction.source_id == source_id,
                PendingAction.action_type == "RemoveKnowledge",
                PendingAction.proposal_fingerprint == fingerprint,
            )
        )
        if existing is not None:
            return existing
        document = session.get(KnowledgeDocument, arguments.document_id)
        if document is None or document.user_id != user_id:
            raise LookupError(arguments.document_id)
        now = services.clock.now()
        action_id = str(services.ids.new_id())
        action = PendingAction(
            id=action_id,
            user_id=user_id,
            source_type="agent_tool",
            source_id=source_id,
            action_type="RemoveKnowledge",
            resolved_arguments=payload,
            display_summary=f"移除知识: {document.title}",
            proposal_fingerprint=fingerprint,
            confirmation_code=_confirmation_code(action_id),
            state=PendingActionState.PENDING.value,
            created_at=now,
            expires_at=now + timedelta(days=7),
            attempt_count=0,
        )
        session.add(action)
        session.add(_created_event(services, action))
        return action
