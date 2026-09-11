"""Durable mail ingestion and effective analysis persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select

from jobs_status_manager.application_core.domain import EventType, OutboxStatus
from jobs_status_manager.application_core.models import OutboxEvent
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.safe_errors import safe_external_error
from jobs_status_manager.mail import (
    AnalysisState,
    JobMailAnalysisInput,
    MailClassification,
    MailEnvelope,
    analysis_fingerprint,
    bounded_prompt,
    classify_mail,
)
from jobs_status_manager.mail_models import JobMailAnalysis, Mail, ProcessedEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from jobs_status_manager.infrastructure.adapters.protocols import LLMAdapter
    from jobs_status_manager.infrastructure.clock import Clock
    from jobs_status_manager.infrastructure.database.connection import Database
    from jobs_status_manager.infrastructure.ids import IdGenerator


@dataclass(frozen=True, slots=True)
class MailServices:
    """Shared synchronous service dependencies."""

    database: Database
    clock: Clock
    ids: IdGenerator


@dataclass(frozen=True, slots=True)
class AnalysisMetadata:
    """Versioned analysis provenance."""

    analysis_version: str = "v1"
    model_name: str = "fake"
    prompt_version: str = "mail-v1"


@dataclass(frozen=True, slots=True)
class ConsumerMarker:
    """Processed-event identity committed with a consumer effect."""

    consumer_name: str
    event_id: str


@dataclass(frozen=True, slots=True)
class MailIngestion:
    """Result of idempotently ingesting one provider message."""

    mail: Mail
    created: bool


def _event(services: MailServices, event_type: EventType, aggregate_id: str) -> OutboxEvent:
    now = services.clock.now()
    event_id = str(services.ids.new_id())
    return OutboxEvent(
        id=event_id,
        event_id=event_id,
        event_type=event_type.value,
        event_version=1,
        aggregate_type="Mail" if event_type is EventType.MAIL_RECEIVED else "JobMailAnalysis",
        aggregate_id=aggregate_id,
        producer="mail_pipeline",
        payload={"entity_id": aggregate_id},
        occurred_at=now,
        status=OutboxStatus.PENDING.value,
        created_at=now,
        attempt_count=0,
    )


def ingest_mail(
    services: MailServices,
    ownership: tuple[str, str],
    envelope: MailEnvelope,
) -> MailIngestion:
    """Persist one mail and receipt event atomically and idempotently."""
    user_id, mail_account_id = ownership
    with transaction(services.database) as session:
        existing = session.scalar(
            select(Mail).where(
                Mail.mail_account_id == mail_account_id,
                Mail.provider_message_id == envelope.provider_message_id,
            )
        )
        if existing is not None:
            return MailIngestion(mail=existing, created=False)
        now = services.clock.now()
        mail = Mail(
            id=str(services.ids.new_id()),
            user_id=user_id,
            mail_account_id=mail_account_id,
            provider_message_id=envelope.provider_message_id,
            subject=envelope.subject[:2000],
            sender=envelope.sender[:1000],
            recipients=list(envelope.recipients[:100]),
            received_at=envelope.received_at,
            content=envelope.content[:12000],
            processing_state=AnalysisState.PENDING.value,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(mail)
        session.add(_event(services, EventType.MAIL_RECEIVED, mail.id))
        return MailIngestion(mail=mail, created=True)


def classify_stored_mail(mail: Mail) -> MailClassification:
    """Classify persisted mail without external services."""
    return classify_mail(mail.subject, mail.content)


def analyze_mail(
    services: MailServices,
    mail_id: str,
    llm: LLMAdapter,
    metadata: AnalysisMetadata | None = None,
    marker: ConsumerMarker | None = None,
) -> JobMailAnalysis | None:
    """Call the LLM outside transactions and persist a validated result."""
    effective_metadata = metadata if metadata is not None else AnalysisMetadata()
    with transaction(services.database) as session:
        mail = session.get(Mail, mail_id)
        if mail is None:
            return None
        classification = classify_stored_mail(mail)
        if not classification.is_job_mail:
            mail.processing_state = "NON_JOB"
            mail.updated_at = services.clock.now()
            _add_marker(services, session, marker)
            return None
        prompt = bounded_prompt(mail.subject, mail.content)
    try:
        analysis = llm.analyze_job_mail(prompt)
    except (RuntimeError, ValidationError) as error:
        mark_analysis_failure(services, mail_id, safe_external_error(error))
        raise
    return persist_analysis(services, mail_id, analysis, effective_metadata, marker)


def persist_analysis(
    services: MailServices,
    mail_id: str,
    analysis: JobMailAnalysisInput,
    metadata: AnalysisMetadata,
    marker: ConsumerMarker | None = None,
) -> JobMailAnalysis:
    """Upsert the effective analysis and emit only its first durable event."""
    with transaction(services.database) as session:
        mail = session.get(Mail, mail_id)
        if mail is None:
            raise LookupError(mail_id)
        effective = session.scalar(
            select(JobMailAnalysis).where(JobMailAnalysis.mail_id == mail_id)
        )
        now = services.clock.now()
        if effective is None:
            effective = JobMailAnalysis(
                id=str(services.ids.new_id()),
                mail_id=mail_id,
                mail_type=analysis.mail_type.value,
                application=analysis.application.model_dump(mode="json"),
                status_suggestion=analysis.status_suggestion.model_dump(mode="json"),
                details=analysis.details.model_dump(mode="json"),
                summary=analysis.summary,
                confidence=analysis.confidence.model_dump(mode="json"),
                analysis_version=metadata.analysis_version,
                model_name=metadata.model_name,
                prompt_version=metadata.prompt_version,
                fingerprint=analysis_fingerprint(analysis),
                analyzed_at=now,
            )
            session.add(effective)
            session.add(_event(services, EventType.JOB_MAIL_ANALYZED, effective.id))
        effective.mail_type = analysis.mail_type.value
        effective.application = analysis.application.model_dump(mode="json")
        effective.status_suggestion = analysis.status_suggestion.model_dump(mode="json")
        effective.details = analysis.details.model_dump(mode="json")
        effective.summary = analysis.summary
        effective.confidence = analysis.confidence.model_dump(mode="json")
        effective.analysis_version = metadata.analysis_version
        effective.model_name = metadata.model_name
        effective.prompt_version = metadata.prompt_version
        effective.fingerprint = analysis_fingerprint(analysis)
        effective.analyzed_at = now
        mail.processing_state = AnalysisState.SUCCEEDED.value
        mail.attempt_count += 1
        mail.last_error = None
        mail.next_retry_at = None
        mail.updated_at = now
        _add_marker(services, session, marker)
        return effective


def _add_marker(services: MailServices, session: Session, marker: ConsumerMarker | None) -> None:
    """Add an idempotency marker inside the current consumer-effect transaction."""
    if marker is not None:
        session.add(
            ProcessedEvent(
                id=str(services.ids.new_id()),
                consumer_name=marker.consumer_name,
                event_id=marker.event_id,
                processed_at=services.clock.now(),
            )
        )


def mark_analysis_failure(services: MailServices, mail_id: str, error: str) -> None:
    """Persist bounded retry state after an analysis failure."""
    with transaction(services.database) as session:
        mail = session.get(Mail, mail_id)
        if mail is None:
            return
        now = services.clock.now()
        mail.processing_state = AnalysisState.RETRY_WAIT.value
        mail.attempt_count += 1
        mail.last_error = error[:2000]
        mail.next_retry_at = now + timedelta(minutes=5)
        mail.updated_at = now
