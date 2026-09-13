"""Persisted outbox consumer handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from jobs_status_manager.application_core.domain import StatusUpdateArguments, UserId
from jobs_status_manager.application_core.models import PendingAction
from jobs_status_manager.application_core.proposals import create_status_proposal
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.mail import JobMailAnalysisInput
from jobs_status_manager.mail_models import JobMailAnalysis, Mail, ProcessedEvent
from jobs_status_manager.mail_service import AnalysisMetadata, ConsumerMarker, analyze_mail
from jobs_status_manager.notification_models import Notification
from jobs_status_manager.notifications import (
    NotificationState,
    confirmation_content,
    ordinary_content,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from jobs_status_manager.event_pipeline import EventServices


def _processed(session: Session, consumer: str, event_id: str) -> bool:
    return (
        session.scalar(
            select(ProcessedEvent.id).where(
                ProcessedEvent.consumer_name == consumer,
                ProcessedEvent.event_id == event_id,
            )
        )
        is not None
    )


def consume_mail_received(services: EventServices, event_id: str, mail_id: str) -> None:
    """Analyze one received mail unless this consumer already processed it."""
    consumer = "mail-analysis"
    with transaction(services.mail.database) as session:
        if _processed(session, consumer, event_id):
            return
        if session.get(Mail, mail_id) is None:
            message = f"mail not found: {mail_id}"
            raise RuntimeError(message)
    analyze_mail(
        services.mail,
        mail_id,
        services.llm,
        metadata=AnalysisMetadata(model_name=getattr(services.llm, "model_name", "fake")),
        marker=ConsumerMarker(consumer_name=consumer, event_id=event_id),
    )


def consume_job_analyzed(services: EventServices, event_id: str, analysis_id: str) -> None:
    """Route one analyzed mail to a proposal or notification."""
    consumer = "job-analysis-routing"
    with transaction(services.mail.database) as session:
        if _processed(session, consumer, event_id):
            return
        analysis = session.get(JobMailAnalysis, analysis_id)
        if analysis is None:
            message = f"job mail analysis not found: {analysis_id}"
            raise RuntimeError(message)
        mail = session.get(Mail, analysis.mail_id)
        if mail is None:
            message = f"mail not found for analysis: {analysis.id}"
            raise RuntimeError(message)
        parsed = _parse_analysis(analysis)
        suggestion = parsed.status_suggestion
        if suggestion.should_update:
            arguments = StatusUpdateArguments.model_validate(
                {
                    **parsed.application.model_dump(),
                    "status": suggestion.status,
                    "interview_round": suggestion.interview_round,
                }
            )
            create_status_proposal(
                session,
                user_id=UserId(mail.user_id),
                source_id=analysis.id,
                arguments=arguments,
                clock=services.mail.clock,
                ids=services.mail.ids,
                source_type="mail_analysis",
                mail_analysis_id=analysis.id,
            )
        else:
            app = parsed.application
            session.add(
                Notification(
                    id=str(services.mail.ids.new_id()),
                    user_id=mail.user_id,
                    type="JOB_MAIL",
                    channel="QQ_PUSH",
                    title="求职邮件",
                    content=ordinary_content(app.company, app.position, parsed.summary),
                    source_event_id=event_id,
                    related_mail_id=mail.id,
                    state=NotificationState.PENDING.value,
                    attempt_count=0,
                    created_at=services.mail.clock.now(),
                    updated_at=services.mail.clock.now(),
                )
            )
        session.add(
            ProcessedEvent(
                id=str(services.mail.ids.new_id()),
                consumer_name=consumer,
                event_id=event_id,
                processed_at=services.mail.clock.now(),
            )
        )


def _parse_analysis(analysis: JobMailAnalysis) -> JobMailAnalysisInput:
    """Parse persisted JSON at the database trust boundary."""
    return JobMailAnalysisInput.model_validate(
        {
            "application": analysis.application,
            "mail_type": analysis.mail_type,
            "status_suggestion": analysis.status_suggestion,
            "details": analysis.details,
            "summary": analysis.summary,
            "confidence": analysis.confidence,
        }
    )


def consume_action_created(services: EventServices, event_id: str, action_id: str) -> None:
    """Create one confirmation notification for a pending action."""
    consumer = "pending-action-notification"
    with transaction(services.mail.database) as session:
        if _processed(session, consumer, event_id):
            return
        action = session.get(PendingAction, action_id)
        if action is None:
            message = f"pending action not found: {action_id}"
            raise RuntimeError(message)
        if action.mail_analysis_id is None:
            message = f"pending action has no mail analysis: {action_id}"
            raise RuntimeError(message)
        analysis = session.get(JobMailAnalysis, action.mail_analysis_id)
        if analysis is None:
            message = f"job mail analysis not found: {action.mail_analysis_id}"
            raise RuntimeError(message)
        mail = session.get(Mail, analysis.mail_id)
        if mail is None:
            message = f"mail not found for analysis: {analysis.id}"
            raise RuntimeError(message)
        arguments = StatusUpdateArguments.model_validate(action.resolved_arguments)
        session.add(
            Notification(
                id=str(services.mail.ids.new_id()),
                user_id=mail.user_id,
                type="PENDING_ACTION_CONFIRMATION",
                channel="QQ_PUSH",
                title="请确认状态更新",
                content=confirmation_content(
                    arguments.company,
                    arguments.position,
                    arguments.status.value,
                    action.confirmation_code,
                    arguments.interview_round,
                ),
                source_event_id=event_id,
                related_mail_id=mail.id,
                related_pending_action_id=action.id,
                state=NotificationState.PENDING.value,
                attempt_count=0,
                created_at=services.mail.clock.now(),
                updated_at=services.mail.clock.now(),
            )
        )
        session.add(
            ProcessedEvent(
                id=str(services.mail.ids.new_id()),
                consumer_name=consumer,
                event_id=event_id,
                processed_at=services.mail.clock.now(),
            )
        )
