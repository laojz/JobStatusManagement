"""Application and Mail read-tool execution."""

from typing import Literal, assert_never

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobs_status_manager.agent.tool_serializers import (
    JsonValue,
    _application_data,
    _json_data,
    _mail_data,
)
from jobs_status_manager.agent.write_contracts import ToolExecution
from jobs_status_manager.application_core.models import Application, JobEvent
from jobs_status_manager.mail_models import JobMailAnalysis, Mail

type ToolArguments = dict[str, str | int | bool | None]
type ApplicationToolName = Literal[
    "SearchApplications", "GetApplication", "GetApplicationStatus", "GetStatusHistory"
]


def execute_application(
    session: Session,
    user_id: str,
    name: ApplicationToolName,
    arguments: ToolArguments,
) -> ToolExecution:
    """Execute one relational Application query."""
    if name == "SearchApplications":
        return _search_applications(session, user_id, arguments)
    application_id = arguments.get("application_id")
    if not isinstance(application_id, str):
        return ToolExecution("null", {})
    application = session.scalar(
        select(Application).where(
            Application.user_id == user_id,
            Application.id == application_id,
        )
    )
    if application is None:
        return ToolExecution("null", {})
    refs: dict[str, str | None] = {"application_id": application.id}
    match name:
        case "GetApplication":
            return ToolExecution(_json_data(_application_data(application)), refs)
        case "GetApplicationStatus":
            status_value: dict[str, JsonValue] = {
                "status": application.current_status,
                "interview_round": application.current_interview_round,
            }
            return ToolExecution(_json_data(status_value), refs)
        case "GetStatusHistory":
            events = list(
                session.scalars(
                    select(JobEvent)
                    .where(JobEvent.application_id == application.id)
                    .order_by(JobEvent.created_at)
                )
            )
            value: list[JsonValue] = [
                {
                    "status": event.current_status,
                    "round": event.current_interview_round,
                }
                for event in events
            ]
            return ToolExecution(_json_data(value), refs)
        case unreachable:
            assert_never(unreachable)


def _search_applications(
    session: Session,
    user_id: str,
    arguments: ToolArguments,
) -> ToolExecution:
    rows = list(session.scalars(select(Application).where(Application.user_id == user_id)))
    company = arguments.get("company")
    if isinstance(company, str):
        rows = [row for row in rows if company.casefold() in row.company.casefold()]
    return ToolExecution(_json_data([_application_data(row) for row in rows]), {})


def execute_mail(
    session: Session,
    user_id: str,
    name: str,
    arguments: ToolArguments,
) -> ToolExecution:
    """Execute one relational Mail query."""
    if name == "SearchMails":
        return _search_mails(session, user_id, arguments)
    if name == "GetRecentMails":
        rows = list(
            session.scalars(
                select(Mail)
                .where(Mail.user_id == user_id)
                .order_by(Mail.received_at.desc())
                .limit(5)
            )
        )
        return ToolExecution(_json_data([_mail_data(row) for row in rows]), {})
    mail_id = arguments.get("mail_id")
    if not isinstance(mail_id, str):
        return ToolExecution("null", {})
    mail = session.scalar(select(Mail).where(Mail.user_id == user_id, Mail.id == mail_id))
    if mail is None:
        return ToolExecution("null", {})
    refs: dict[str, str | None] = {"mail_id": mail.id}
    if name == "GetMailContent":
        return ToolExecution(_json_data({"subject": mail.subject, "content": mail.content}), refs)
    analysis = session.scalar(select(JobMailAnalysis).where(JobMailAnalysis.mail_id == mail.id))
    analysis_value: JsonValue = None
    if analysis is not None:
        analysis_value = {
            "summary": analysis.summary,
            "mail_type": analysis.mail_type,
            "status_suggestion": {
                "should_update": analysis.status_suggestion.get("should_update"),
                "status": analysis.status_suggestion.get("status"),
            },
        }
    return ToolExecution(_json_data(analysis_value), refs)


def _search_mails(session: Session, user_id: str, arguments: ToolArguments) -> ToolExecution:
    rows = list(
        session.scalars(
            select(Mail).where(Mail.user_id == user_id).order_by(Mail.received_at.desc())
        )
    )
    query = arguments.get("query")
    if isinstance(query, str):
        rows = [
            row for row in rows if query.casefold() in f"{row.subject} {row.content}".casefold()
        ]
    return ToolExecution(_json_data([_mail_data(row) for row in rows]), {})
