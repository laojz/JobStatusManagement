import json
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from jobs_status_manager.agent.contracts import (
    AgentRunState,
    ConversationResponse,
)
from jobs_status_manager.agent.runtime import RuntimeServices, process_run
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeLLM, FakeQQGateway
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import UUIDGenerator

from .malformed_write_support import seed_run, write_request


@dataclass(frozen=True, slots=True)
class RecoveryCase:
    name: str
    arguments: dict[str, str | int | bool | None]
    reason_code: str
    affected_fields: list[str]


@pytest.mark.parametrize(
    "case",
    [
        RecoveryCase(
            "UpdateApplicationStatus",
            {"company": "company-secret", "status": "APPLIED"},
            "MISSING_REQUIRED_FIELD",
            ["position"],
        ),
        RecoveryCase(
            "UpdateApplicationStatus",
            {"company": "company-secret", "position": "position-secret"},
            "MISSING_REQUIRED_FIELD",
            ["status"],
        ),
        RecoveryCase(
            "UpdateApplicationStatus",
            {
                "company": "company-secret",
                "position": "position-secret",
                "status": "INVALID_SECRET_STATUS",
            },
            "INVALID_STATUS",
            ["status"],
        ),
        RecoveryCase(
            "UpdateApplicationStatus",
            {
                "company": "company-secret",
                "position": "position-secret",
                "status": "INTERVIEW",
                "interview_round": 0,
            },
            "INVALID_INTERVIEW_ROUND",
            ["interview_round"],
        ),
        RecoveryCase(
            "UpdateApplicationStatus",
            {
                "company": "company-secret",
                "position": "position-secret",
                "status": "APPLIED",
                "interview_round": 1,
            },
            "INVALID_FIELD_COMBINATION",
            ["status", "interview_round"],
        ),
        RecoveryCase(
            "AddKnowledge",
            {
                "source_file_id": "source-secret",
                "title": "title-secret",
                "document_type": "INVALID_SECRET_TYPE",
                "content": "content-secret",
            },
            "INVALID_DOCUMENT_TYPE",
            ["document_type"],
        ),
        RecoveryCase("RemoveKnowledge", {}, "MISSING_REQUIRED_FIELD", ["document_id"]),
    ],
)
def test_malformed_write_is_handled_with_tool_specific_safe_metadata(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    case: RecoveryCase,
) -> None:
    # Given
    seed_run(database, settings, fake_clock)
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(tool_calls=(write_request(case.name, case.arguments),))
        ]
    )
    qq = FakeQQGateway()

    # When
    process_run(RuntimeServices(database, llm, qq, fake_clock, UUIDGenerator()), "malformed-run")

    # Then
    with database.engine.connect() as connection:
        run = connection.execute(
            text("SELECT state, error FROM agent_runs WHERE id='malformed-run'")
        ).one()
        result = connection.execute(text("SELECT data, error FROM tool_results")).one()
        pending_count = connection.execute(
            text("SELECT COUNT(*) FROM pending_actions")
        ).scalar_one()
    metadata = json.loads(result.data)
    assert (run.state, run.error) == (AgentRunState.COMPLETED.value, None)
    assert result.error is None
    assert metadata["outcome"] == "CLARIFICATION_REQUIRED"
    assert metadata["tool_name"] == case.name
    assert metadata["reason_code"] == case.reason_code
    assert metadata["affected_fields"] == case.affected_fields
    assert metadata["recovery_attempt"] == metadata["recovery_limit"] == 1
    assert pending_count == 0
    secret_values = [
        value
        for value in case.arguments.values()
        if isinstance(value, str) and "secret" in value.lower()
    ]
    assert not any(value in result.data for value in secret_values)
    assert [call[0] for call in llm.calls] == ["converse"]
    assert [call[0] for call in qq.calls] == ["deliver"]
