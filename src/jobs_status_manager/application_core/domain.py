"""Phase 1 application value objects and deterministic rules."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, NewType

from pydantic import BaseModel, ConfigDict, Field, model_validator

UserId = NewType("UserId", str)
ApplicationId = NewType("ApplicationId", str)
PendingActionId = NewType("PendingActionId", str)


@unique
class ApplicationStatus(StrEnum):
    """All v1 application statuses."""

    APPLIED = "APPLIED"
    SCREENING = "SCREENING"
    ASSESSMENT = "ASSESSMENT"
    INTERVIEW = "INTERVIEW"
    HR_INTERVIEW = "HR_INTERVIEW"
    OFFER = "OFFER"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    REJECTED = "REJECTED"
    TERMINATED = "TERMINATED"


@unique
class PendingActionState(StrEnum):
    """Persistent PendingAction lifecycle states."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@unique
class OutboxStatus(StrEnum):
    """Transactional outbox delivery state."""

    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


@unique
class EventType(StrEnum):
    """Phase 1 persisted domain events."""

    MAIL_RECEIVED = "MAIL_RECEIVED"
    JOB_MAIL_ANALYZED = "JOB_MAIL_ANALYZED"
    PENDING_ACTION_CREATED = "PENDING_ACTION_CREATED"
    PENDING_ACTION_RESOLVED = "PENDING_ACTION_RESOLVED"
    APPLICATION_STATUS_CHANGED = "APPLICATION_STATUS_CHANGED"
    KNOWLEDGE_DOCUMENT_ADDED = "KNOWLEDGE_DOCUMENT_ADDED"
    KNOWLEDGE_DOCUMENT_REMOVED = "KNOWLEDGE_DOCUMENT_REMOVED"


@unique
class ActionResolution(StrEnum):
    """Confirmation outcomes understood by the router."""

    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")
_PUNCTUATION: Final = str.maketrans(
    {
        "，": ",",  # noqa: RUF001
        "。": ".",
        "：": ":",  # noqa: RUF001
        "；": ";",  # noqa: RUF001
        "！": "!",  # noqa: RUF001
        "？": "?",  # noqa: RUF001
        "（": "(",  # noqa: RUF001
        "）": ")",  # noqa: RUF001
        "【": "[",
        "】": "]",
        "“": '"',
        "”": '"',
        "‘": "'",  # noqa: RUF001
        "’": "'",  # noqa: RUF001
        "－": "-",  # noqa: RUF001
        "—": "-",
        "–": "-",  # noqa: RUF001
    }
)


def normalize_key(value: str) -> str:
    """Normalize a business key without translation or fuzzy matching."""
    normalized = unicodedata.normalize("NFKC", value).translate(_PUNCTUATION)
    normalized = _WHITESPACE.sub(" ", normalized).strip().casefold()
    return re.sub(r"\s*([,.():;!?\[\]])\s*", r"\1", normalized)


@dataclass(frozen=True, slots=True)
class ApplicationReference:
    """Display and normalized fields identifying one application."""

    company: str
    department: str | None
    position: str
    company_key: str
    department_key: str
    position_key: str

    @classmethod
    def from_display(
        cls, company: str, department: str | None, position: str
    ) -> ApplicationReference:
        """Build an exact-match reference from display values."""
        display_department = department.strip() if department is not None else None
        return cls(
            company=company.strip(),
            department=display_department or None,
            position=position.strip(),
            company_key=normalize_key(company),
            department_key=normalize_key(department or ""),
            position_key=normalize_key(position),
        )


class StatusUpdateArguments(BaseModel):
    """Frozen, validated arguments for UpdateApplicationStatus."""

    model_config = ConfigDict(frozen=True)

    company: str = Field(min_length=1)
    department: str | None = None
    position: str = Field(min_length=1)
    status: ApplicationStatus
    interview_round: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_interview_round(self) -> StatusUpdateArguments:
        """Require a positive round only for interview status."""
        if self.status is ApplicationStatus.INTERVIEW and self.interview_round is None:
            message = "INTERVIEW requires a positive interview_round"
            raise ValueError(message)
        if self.status is not ApplicationStatus.INTERVIEW and self.interview_round is not None:
            message = "non-INTERVIEW status must not have interview_round"
            raise ValueError(message)
        return self

    def reference(self) -> ApplicationReference:
        """Return the exact-match application reference."""
        return ApplicationReference.from_display(self.company, self.department, self.position)


def proposal_fingerprint(arguments: StatusUpdateArguments) -> str:
    """Return a stable fingerprint of normalized resolved arguments."""
    payload = {
        "companyKey": arguments.reference().company_key,
        "departmentKey": arguments.reference().department_key,
        "positionKey": arguments.reference().position_key,
        "status": arguments.status.value,
        "interviewRound": arguments.interview_round,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def status_changed(
    previous_status: ApplicationStatus | None,
    previous_round: int | None,
    current_status: ApplicationStatus,
    current_round: int | None,
) -> bool:
    """Report whether an application event is required."""
    return previous_status != current_status or previous_round != current_round
