import json

import pytest

from jobs_status_manager.agent.runtime_support import PendingToolCall
from jobs_status_manager.agent.write_preflight import (
    preflight_confirmation_writes,
    recovery_result_data,
)


@pytest.mark.parametrize(
    ("arguments", "reason_code", "fields"),
    [
        ({}, "MISSING_REQUIRED_FIELD", ("company", "position", "status")),
        (
            {"company": "secret", "position": "secret", "status": "INTERVIEW"},
            "MISSING_REQUIRED_FIELD",
            ("interview_round",),
        ),
        (
            {
                "company": "secret",
                "position": "secret",
                "status": "INTERVIEW",
                "interview_round": -1,
            },
            "INVALID_INTERVIEW_ROUND",
            ("interview_round",),
        ),
        (
            {
                "company": "secret",
                "position": "secret",
                "status": "APPLIED",
                "interview_round": 1,
            },
            "INVALID_FIELD_COMBINATION",
            ("status", "interview_round"),
        ),
        (
            {"company": "secret", "position": "secret", "status": "invalid-secret"},
            "INVALID_STATUS",
            ("status",),
        ),
    ],
)
def test_status_preflight_classifies_safe_recovery(
    arguments: dict[str, str | int | bool | None],
    reason_code: str,
    fields: tuple[str, ...],
) -> None:
    malformed = preflight_confirmation_writes(
        (PendingToolCall("call-id", "UpdateApplicationStatus", arguments),)
    )

    assert malformed is not None
    assert malformed.reason_code == reason_code
    assert malformed.affected_fields == fields


def test_add_knowledge_preflight_validates_domain_arguments() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "call-id",
                "AddKnowledge",
                {
                    "source_file_id": "source-secret",
                    "title": "title-secret",
                    "document_type": "invalid-secret",
                    "content": "content-secret",
                },
            ),
        )
    )

    assert malformed is not None
    assert malformed.tool_name == "AddKnowledge"
    assert malformed.reason_code == "INVALID_DOCUMENT_TYPE"
    assert malformed.affected_fields == ("document_type",)


def test_remove_knowledge_metadata_omits_unknown_field_and_value() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "call-id",
                "RemoveKnowledge",
                {"unknown-secret-field": "secret-value"},
            ),
        )
    )

    assert malformed is not None
    metadata = recovery_result_data("call-id", "RemoveKnowledge", malformed)
    payload = json.loads(metadata)
    assert payload["tool_name"] == "RemoveKnowledge"
    assert payload["reason_code"] == "MISSING_REQUIRED_FIELD"
    assert payload["validation_locations"] == ["document_id"]
    assert payload["validation_types"] == ["missing"]
    assert "unknown-secret-field" not in metadata
    assert "secret-value" not in metadata


def test_status_metadata_uses_only_safe_root_location_and_type() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "malformed-call",
                "UpdateApplicationStatus",
                {
                    "company": "company-secret",
                    "position": "position-secret",
                    "status": "INTERVIEW",
                },
            ),
        )
    )

    assert malformed is not None
    metadata = recovery_result_data(
        "malformed-call",
        "UpdateApplicationStatus",
        malformed,
    )
    payload = json.loads(metadata)
    assert payload["missing_fields"] == ["interview_round"]
    assert payload["validation_locations"] == ["$"]
    assert payload["validation_types"] == ["value_error"]
    assert "company-secret" not in metadata
    assert "position-secret" not in metadata


def test_status_extra_field_has_actionable_generic_clarification() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "malformed-call",
                "UpdateApplicationStatus",
                {
                    "company": "company",
                    "position": "position",
                    "status": "APPLIED",
                    "unexpected": "secret-value",
                },
            ),
        )
    )

    assert malformed is not None
    assert malformed.reason_code == "INVALID_ARGUMENT"
    assert malformed.affected_fields == ("arguments",)
    assert "调用参数" in malformed.clarification
    assert malformed.clarification != "请补充或确认以下信息: 。"


def test_status_invalid_department_has_actionable_field_clarification() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "malformed-call",
                "UpdateApplicationStatus",
                {
                    "company": "company",
                    "department": True,
                    "position": "position",
                    "status": "APPLIED",
                },
            ),
        )
    )

    assert malformed is not None
    assert malformed.reason_code == "INVALID_ARGUMENT"
    assert malformed.affected_fields == ("department",)
    assert "部门" in malformed.clarification


def test_same_tool_sibling_is_not_mistaken_for_malformed_trigger() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall(
                "valid-call",
                "UpdateApplicationStatus",
                {
                    "company": "company",
                    "position": "position",
                    "status": "APPLIED",
                },
            ),
            PendingToolCall("malformed-call", "UpdateApplicationStatus", {}),
        )
    )

    assert malformed is not None
    sibling = json.loads(recovery_result_data("valid-call", "UpdateApplicationStatus", malformed))
    assert sibling["outcome"] == "NOT_EXECUTED"
    assert "affected_fields" not in sibling


def test_preflight_valid_batch_returns_no_recovery() -> None:
    malformed = preflight_confirmation_writes(
        (
            PendingToolCall("read", "GetRecentMails", {}),
            PendingToolCall(
                "status",
                "UpdateApplicationStatus",
                {
                    "company": "company",
                    "position": "position",
                    "status": "INTERVIEW",
                    "interview_round": 1,
                },
            ),
            PendingToolCall(
                "add",
                "AddKnowledge",
                {
                    "source_file_id": "source",
                    "title": "title",
                    "document_type": "COMPANY_KNOWLEDGE",
                    "content": "content",
                },
            ),
            PendingToolCall("remove", "RemoveKnowledge", {"document_id": "document"}),
        )
    )

    assert malformed is None
