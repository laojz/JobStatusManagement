"""Side-effect-free validation for confirmation-gated write batches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, assert_never

from pydantic import TypeAdapter, ValidationError

from jobs_status_manager.agent.tools import TOOL_ARGUMENT_MODELS, WRITE_TOOL_NAMES, WriteToolName
from jobs_status_manager.agent.write_contracts import UpdateApplicationStatusArguments
from jobs_status_manager.knowledge.contracts import RemoveKnowledgeArguments
from jobs_status_manager.knowledge.tooling import AddKnowledgeToolArguments

if TYPE_CHECKING:
    from jobs_status_manager.agent.runtime_support import PendingToolCall

type WriteReasonCode = Literal[
    "MISSING_REQUIRED_FIELD",
    "INVALID_STATUS",
    "INVALID_INTERVIEW_ROUND",
    "INVALID_FIELD_COMBINATION",
    "INVALID_DOCUMENT_TYPE",
    "INVALID_ARGUMENT",
]

_FIELD_ORDER: Final = {
    "UpdateApplicationStatus": (
        "company",
        "position",
        "status",
        "interview_round",
        "department",
    ),
    "AddKnowledge": (
        "source_file_id",
        "title",
        "document_type",
        "content",
        "company",
        "department",
        "position",
        "knowledge_domain",
        "tags",
        "embedding_model",
        "embedding_version",
    ),
    "RemoveKnowledge": ("document_id",),
}
_REQUIRED_FIELDS: Final = {
    "UpdateApplicationStatus": ("company", "position", "status"),
    "AddKnowledge": ("source_file_id", "title", "document_type", "content"),
    "RemoveKnowledge": ("document_id",),
}
_RECOVERY_ATTEMPT: Final = 1
_RECOVERY_LIMIT: Final = 1


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One bounded Pydantic location/type pair."""

    location: str
    error_type: str


@dataclass(frozen=True, slots=True)
class MalformedWrite:
    """Safe recovery classification for one pending write."""

    internal_tool_call_id: str
    tool_name: WriteToolName
    reason_code: WriteReasonCode
    affected_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    validation_issues: tuple[ValidationIssue, ...]
    clarification: str


def _validation_issues(
    error: ValidationError,
    allowed_fields: tuple[str, ...],
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:16]:
        location = detail["loc"]
        field = location[0] if len(location) == 1 and isinstance(location[0], str) else "$"
        issues.append(
            ValidationIssue(
                location=field if field in allowed_fields else "$",
                error_type=detail["type"][:64],
            )
        )
    return tuple(issues)


def _ordered_fields(tool_name: WriteToolName, fields: set[str]) -> tuple[str, ...]:
    return tuple(field for field in _FIELD_ORDER[tool_name] if field in fields)


def _clarification(
    tool_name: WriteToolName,
    reason_code: WriteReasonCode,
    affected_fields: tuple[str, ...],
) -> str:
    match tool_name:
        case "UpdateApplicationStatus":
            labels = {
                "company": "公司",
                "position": "岗位",
                "status": "求职状态",
                "interview_round": "面试轮次",
                "department": "部门",
                "arguments": "调用参数",
            }
            match reason_code:
                case "INVALID_STATUS":
                    clarification = "请确认要记录的求职状态。"
                case "INVALID_INTERVIEW_ROUND":
                    clarification = "请提供大于 0 的面试轮次。"
                case "INVALID_FIELD_COMBINATION":
                    clarification = "请确认求职状态; 只有面试状态需要提供面试轮次。"
                case "MISSING_REQUIRED_FIELD" | "INVALID_DOCUMENT_TYPE" | "INVALID_ARGUMENT":
                    requested = "、".join(
                        labels[field] for field in affected_fields if field in labels
                    )
                    clarification = f"请补充或确认以下信息: {requested}。"
                case unreachable:
                    assert_never(unreachable)
        case "AddKnowledge":
            match reason_code:
                case "INVALID_DOCUMENT_TYPE":
                    clarification = "请确认要添加的知识文档类型。"
                case (
                    "MISSING_REQUIRED_FIELD"
                    | "INVALID_STATUS"
                    | "INVALID_INTERVIEW_ROUND"
                    | "INVALID_FIELD_COMBINATION"
                    | "INVALID_ARGUMENT"
                ):
                    clarification = "请补充或修正要添加的知识文档信息。"
                case unreachable:
                    assert_never(unreachable)
        case "RemoveKnowledge":
            clarification = "请提供要移除的知识文档编号。"
        case unreachable:
            assert_never(unreachable)
    return clarification


def _classify(
    pending: PendingToolCall,
    tool_name: WriteToolName,
    error: ValidationError,
) -> MalformedWrite:
    allowed_fields = _FIELD_ORDER[tool_name]
    issues = _validation_issues(error, allowed_fields)
    missing = {
        issue.location
        for issue in issues
        if issue.error_type == "missing" and issue.location in _REQUIRED_FIELDS[tool_name]
    }
    if (
        tool_name == "UpdateApplicationStatus"
        and pending.arguments.get("status") == "INTERVIEW"
        and pending.arguments.get("interview_round") is None
    ):
        missing.add("interview_round")
    missing_fields = _ordered_fields(tool_name, missing)
    if missing_fields:
        reason_code: WriteReasonCode = "MISSING_REQUIRED_FIELD"
        affected_fields = missing_fields
    elif any(issue.location == "status" and issue.error_type == "enum" for issue in issues):
        reason_code = "INVALID_STATUS"
        affected_fields = ("status",)
    elif any(issue.location == "interview_round" for issue in issues):
        reason_code = "INVALID_INTERVIEW_ROUND"
        affected_fields = ("interview_round",)
    elif (
        tool_name == "UpdateApplicationStatus"
        and pending.arguments.get("status") != "INTERVIEW"
        and pending.arguments.get("interview_round") is not None
    ):
        reason_code = "INVALID_FIELD_COMBINATION"
        affected_fields = ("status", "interview_round")
    elif any(issue.location == "document_type" and issue.error_type == "enum" for issue in issues):
        reason_code = "INVALID_DOCUMENT_TYPE"
        affected_fields = ("document_type",)
    else:
        reason_code = "INVALID_ARGUMENT"
        affected = {issue.location for issue in issues if issue.location != "$"}
        affected_fields = _ordered_fields(tool_name, affected) or ("arguments",)
    return MalformedWrite(
        internal_tool_call_id=pending.internal_tool_call_id,
        tool_name=tool_name,
        reason_code=reason_code,
        affected_fields=affected_fields,
        missing_fields=missing_fields,
        validation_issues=issues,
        clarification=_clarification(tool_name, reason_code, affected_fields),
    )


def _preflight_write(pending: PendingToolCall) -> MalformedWrite | None:
    if pending.name not in WRITE_TOOL_NAMES:
        return None
    tool_name = TypeAdapter(WriteToolName).validate_python(pending.name)
    match tool_name:
        case "UpdateApplicationStatus":
            try:
                UpdateApplicationStatusArguments.model_validate(pending.arguments)
            except ValidationError as error:
                return _classify(pending, tool_name, error)
        case "AddKnowledge":
            tool_name = "AddKnowledge"
            try:
                AddKnowledgeToolArguments.model_validate(pending.arguments).domain_arguments()
            except ValidationError as error:
                return _classify(pending, tool_name, error)
        case "RemoveKnowledge":
            try:
                RemoveKnowledgeArguments.model_validate(pending.arguments)
            except ValidationError as error:
                return _classify(pending, tool_name, error)
        case unreachable:
            assert_never(unreachable)
    return None


def preflight_confirmation_writes(
    pending_calls: tuple[PendingToolCall, ...],
) -> MalformedWrite | None:
    """Validate every pending write and return the first malformed call."""
    first_malformed: MalformedWrite | None = None
    for pending in pending_calls:
        malformed = _preflight_write(pending)
        if first_malformed is None and malformed is not None:
            first_malformed = malformed
    return first_malformed


def recovery_result_data(
    internal_tool_call_id: str,
    tool_name: str,
    malformed: MalformedWrite,
) -> str:
    """Serialize bounded handled metadata without malformed argument values."""
    if internal_tool_call_id != malformed.internal_tool_call_id:
        safe_tool_name = tool_name if tool_name in TOOL_ARGUMENT_MODELS else "UNKNOWN_TOOL"
        payload = {
            "outcome": "NOT_EXECUTED",
            "tool_name": safe_tool_name,
            "reason_code": "PENDING_BATCH_CANCELLED",
            "recovery_attempt": _RECOVERY_ATTEMPT,
            "recovery_limit": _RECOVERY_LIMIT,
        }
    else:
        payload = {
            "outcome": "CLARIFICATION_REQUIRED",
            "tool_name": malformed.tool_name,
            "reason_code": malformed.reason_code,
            "affected_fields": list(malformed.affected_fields),
            "missing_fields": list(malformed.missing_fields),
            "validation_locations": [issue.location for issue in malformed.validation_issues],
            "validation_types": [issue.error_type for issue in malformed.validation_issues],
            "recovery_attempt": _RECOVERY_ATTEMPT,
            "recovery_limit": _RECOVERY_LIMIT,
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
