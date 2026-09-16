import json

from sqlalchemy import text

from jobs_status_manager.agent.contracts import (
    AgentRunState,
    ConversationResponse,
    ProviderError,
    ProviderErrorKind,
)
from jobs_status_manager.agent.runtime import RuntimeServices, process_run
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeLLM,
    FakeQQDeliveryResult,
    FakeQQGateway,
)
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import UUIDGenerator

from .malformed_write_support import seed_run, write_request


def test_malformed_write_cancels_every_pending_sibling_before_execution(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    # Given
    seed_run(database, settings, fake_clock)
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_calls=(
                    write_request(
                        "UpdateApplicationStatus",
                        {
                            "company": "company-secret",
                            "position": "position-secret",
                            "status": "APPLIED",
                        },
                    ),
                    write_request("GetRecentMails", {}),
                    write_request("RemoveKnowledge", {}),
                )
            )
        ]
    )

    # When
    process_run(
        RuntimeServices(database, llm, FakeQQGateway(), fake_clock, UUIDGenerator()),
        "malformed-run",
    )

    # Then
    with database.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tool_calls.tool_name, tool_results.data, tool_results.error "
                "FROM tool_results JOIN tool_calls ON tool_calls.id=tool_results.tool_call_id "
                "ORDER BY tool_calls.sequence"
            )
        ).all()
        pending_count = connection.execute(
            text("SELECT COUNT(*) FROM pending_actions")
        ).scalar_one()
    assert len(rows) == 3
    valid_write_metadata = json.loads(rows[0].data)
    read_metadata = json.loads(rows[1].data)
    malformed_metadata = json.loads(rows[2].data)
    assert (rows[0].tool_name, rows[0].error) == ("UpdateApplicationStatus", None)
    assert valid_write_metadata == {
        "outcome": "NOT_EXECUTED",
        "tool_name": "UpdateApplicationStatus",
        "reason_code": "PENDING_BATCH_CANCELLED",
        "recovery_attempt": 1,
        "recovery_limit": 1,
    }
    assert (rows[1].tool_name, rows[1].error) == ("GetRecentMails", None)
    assert read_metadata == {
        "outcome": "NOT_EXECUTED",
        "tool_name": "GetRecentMails",
        "reason_code": "PENDING_BATCH_CANCELLED",
        "recovery_attempt": 1,
        "recovery_limit": 1,
    }
    assert malformed_metadata["outcome"] == "CLARIFICATION_REQUIRED"
    assert malformed_metadata["tool_name"] == "RemoveKnowledge"
    assert malformed_metadata["missing_fields"] == ["document_id"]
    assert "company-secret" not in rows[0].data
    assert "position-secret" not in rows[0].data
    assert pending_count == 0


def test_delivery_retry_reuses_recovery_artifacts_without_reprocessing(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    # Given
    seed_run(database, settings, fake_clock)
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_calls=(
                    write_request(
                        "RemoveKnowledge",
                        {"unexpected_secret_field": "secret-value"},
                    ),
                )
            )
        ]
    )
    retryable_qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(ProviderErrorKind.RETRYABLE, "qq", "temporary"),
        )
    )
    services = RuntimeServices(database, llm, retryable_qq, fake_clock, UUIDGenerator())

    # When
    process_run(services, "malformed-run")
    fake_clock.advance(300)
    successful_qq = FakeQQGateway()
    retry_services = RuntimeServices(database, llm, successful_qq, fake_clock, UUIDGenerator())
    process_run(retry_services, "malformed-run")
    process_run(retry_services, "malformed-run")

    # Then
    with database.engine.connect() as connection:
        run = connection.execute(
            text("SELECT state, error, delivery_state FROM agent_runs WHERE id='malformed-run'")
        ).one()
        counts = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM tool_calls), "
                "(SELECT COUNT(*) FROM tool_results), "
                "(SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'), "
                "(SELECT COUNT(*) FROM pending_actions)"
            )
        ).one()
    assert (run.state, run.error, run.delivery_state) == (
        AgentRunState.COMPLETED.value,
        None,
        "SENT",
    )
    assert counts == (1, 1, 1, 0)
    assert [call[0] for call in llm.calls] == ["converse"]
    assert [call[0] for call in retryable_qq.calls] == ["deliver"]
    assert [call[0] for call in successful_qq.calls] == ["deliver"]


def test_unknown_sibling_is_sanitized_and_does_not_block_delivery_retry(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    # Given
    seed_run(database, settings, fake_clock)
    unknown_name = "UnknownSecretTool_PRIVATE"
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_calls=(
                    write_request("RemoveKnowledge", {}),
                    write_request(unknown_name, {"payload": "secret-value"}),
                )
            )
        ]
    )
    retryable_qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(ProviderErrorKind.RETRYABLE, "qq", "temporary"),
        )
    )

    # When
    process_run(
        RuntimeServices(database, llm, retryable_qq, fake_clock, UUIDGenerator()),
        "malformed-run",
    )
    with database.engine.connect() as connection:
        retry_run = connection.execute(
            text(
                "SELECT state, delivery_state, final_message_id "
                "FROM agent_runs WHERE id='malformed-run'"
            )
        ).one()
        retry_counts = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM tool_results), "
                "(SELECT COUNT(*) FROM conversation_messages WHERE role='assistant')"
            )
        ).one()
        results_per_call = connection.execute(
            text(
                "SELECT tool_call_id, COUNT(*) FROM tool_results "
                "GROUP BY tool_call_id ORDER BY tool_call_id"
            )
        ).all()
    fake_clock.advance(300)
    successful_qq = FakeQQGateway()
    services = RuntimeServices(database, llm, successful_qq, fake_clock, UUIDGenerator())
    process_run(services, "malformed-run")
    process_run(services, "malformed-run")

    # Then
    with database.engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT state, error, delivery_state, final_message_id "
                "FROM agent_runs WHERE id='malformed-run'"
            )
        ).one()
        rows = connection.execute(
            text(
                "SELECT tool_results.data, tool_results.error FROM tool_results "
                "JOIN tool_calls ON tool_calls.id=tool_results.tool_call_id "
                "ORDER BY tool_calls.sequence"
            )
        ).all()
        counts = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM tool_calls), "
                "(SELECT COUNT(*) FROM tool_results), "
                "(SELECT COUNT(*) FROM conversation_messages WHERE role='assistant')"
            )
        ).one()
    sibling_metadata = json.loads(rows[1].data)
    assert retry_run.state == AgentRunState.DELIVERY_PENDING.value
    assert retry_run.delivery_state == "RETRY_WAIT"
    assert retry_run.final_message_id is not None
    assert retry_counts == (2, 1)
    assert [count for _, count in results_per_call] == [1, 1]
    assert (run.state, run.error, run.delivery_state) == (
        AgentRunState.COMPLETED.value,
        None,
        "SENT",
    )
    assert run.final_message_id == retry_run.final_message_id
    assert sibling_metadata["tool_name"] == "UNKNOWN_TOOL"
    assert unknown_name not in rows[1].data
    assert "secret-value" not in rows[1].data
    assert all(row.error is None for row in rows)
    assert counts == (2, 2, 1)
    assert [call[0] for call in llm.calls] == ["converse"]
    assert [call[0] for call in retryable_qq.calls] == ["deliver"]
    assert [call[0] for call in successful_qq.calls] == ["deliver"]
