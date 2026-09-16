import json

from sqlalchemy import text

from jobs_status_manager.agent.contracts import AgentRunState, ConversationResponse, ToolCallRequest
from jobs_status_manager.agent.runtime import RuntimeServices, process_run
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeLLM, FakeQQGateway
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.ids import UUIDGenerator

from .malformed_write_support import seed_run


def test_update_application_status_missing_interview_round_completes_with_clarification(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    # Given
    seed_run(database, settings, fake_clock)
    arguments: dict[str, str | int | bool | None] = {
        "company": "小米",
        "position": "软件研发工程师",
        "status": "INTERVIEW",
    }
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_calls=(
                    ToolCallRequest(
                        provider_call_id="production-shaped-call",
                        provider_type="function",
                        name="UpdateApplicationStatus",
                        arguments=arguments,
                        arguments_json=json.dumps(
                            arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
            )
        ]
    )
    qq = FakeQQGateway()
    with database.engine.connect() as connection:
        initial_outbox_count = connection.execute(
            text("SELECT COUNT(*) FROM outbox_events")
        ).scalar_one()

    # When
    process_run(RuntimeServices(database, llm, qq, fake_clock, UUIDGenerator()), "malformed-run")

    # Then
    with database.engine.connect() as connection:
        run = connection.execute(
            text(
                "SELECT state, error, delivery_state, delivery_error, final_message_id "
                "FROM agent_runs WHERE id='malformed-run'"
            )
        ).one()
        assistant_messages = connection.execute(
            text(
                "SELECT id, content FROM conversation_messages "
                "WHERE session_id='malformed-session' AND role='assistant'"
            )
        ).all()
        tool_results = connection.execute(
            text(
                "SELECT tool_calls.tool_name, tool_results.data, tool_results.error "
                "FROM tool_results JOIN tool_calls ON tool_calls.id=tool_results.tool_call_id"
            )
        ).all()
        mutation_counts = connection.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM pending_actions), "
                "(SELECT COUNT(*) FROM applications), "
                "(SELECT COUNT(*) FROM job_events), "
                "(SELECT COUNT(*) FROM outbox_events)"
            )
        ).one()

    assert run.state == AgentRunState.COMPLETED.value
    assert (run.error, run.delivery_state, run.delivery_error) == (None, "SENT", None)
    assert len(assistant_messages) == 1
    assert run.final_message_id == assistant_messages[0].id
    assert assistant_messages[0].content.strip()
    assert "面试轮次" in assistant_messages[0].content
    assert "面试轮次" in qq.calls[0][1][-1]
    assert len(tool_results) == 1
    assert tool_results[0].tool_name == "UpdateApplicationStatus"
    assert tool_results[0].error is None
    metadata = json.loads(tool_results[0].data)
    assert metadata["outcome"] == "CLARIFICATION_REQUIRED"
    assert metadata["tool_name"] == "UpdateApplicationStatus"
    assert metadata["reason_code"] == "MISSING_REQUIRED_FIELD"
    assert metadata["missing_fields"] == ["interview_round"]
    assert metadata["recovery_attempt"] == metadata["recovery_limit"] == 1
    assert "小米" not in tool_results[0].data
    assert "软件研发工程师" not in tool_results[0].data
    assert mutation_counts == (0, 0, 0, initial_outbox_count)
    assert [call[0] for call in llm.calls] == ["converse"]
    assert [call[0] for call in qq.calls] == ["deliver"]
