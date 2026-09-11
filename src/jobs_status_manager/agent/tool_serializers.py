"""JSON projections returned by read-only Agent tools."""

import json

from jobs_status_manager.application_core.models import Application
from jobs_status_manager.mail_models import Mail

type JsonValue = str | int | float | bool | dict[str, JsonValue] | list[JsonValue] | None


def _json_data(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False)


def _application_data(row: Application) -> dict[str, JsonValue]:
    return {
        "id": row.id,
        "company": row.company,
        "department": row.department,
        "position": row.position,
        "status": row.current_status,
        "interview_round": row.current_interview_round,
    }


def _mail_data(row: Mail) -> dict[str, JsonValue]:
    return {
        "id": row.id,
        "subject": row.subject,
        "sender": row.sender,
        "received_at": row.received_at.isoformat(),
    }
