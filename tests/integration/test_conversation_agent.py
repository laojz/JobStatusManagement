"""Phase 3 durable conversation agent integration tests."""

from datetime import timedelta
from pathlib import Path
from time import sleep
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from starlette.testclient import TestClient

from jobs_status_manager.agent.contracts import (
    AgentRunState,
    ConversationResponse,
    ToolCallRequest,
)
from jobs_status_manager.agent.runtime import (
    RuntimeServices,
    process_run,
    recover_runs,
    resume_confirmed_run,
)
from jobs_status_manager.agent.tools import TOOL_NAMES, WRITE_TOOL_NAMES, definitions
from jobs_status_manager.agent.write_contracts import ToolExecution
from jobs_status_manager.agent.write_recovery import reconcile_terminal_actions
from jobs_status_manager.agent.write_runtime import finalize_rejected_action
from jobs_status_manager.application.lifecycle import LifecycleAdapters, _agent_cycle, create_app
from jobs_status_manager.application_core.domain import (
    ApplicationStatus,
    StatusUpdateArguments,
    UserId,
)
from jobs_status_manager.application_core.proposals import create_status_proposal
from jobs_status_manager.application_core.service import (
    execute_status_update,
    recover_pending_actions,
    resolve_confirmation,
)
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeLLM, FakeQQGateway
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.infrastructure.safe_errors import safe_external_error


def _ids() -> DeterministicIdGenerator:
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(1, 80)]
    )


def _setup(database: Database, settings: AppSettings, clock: FakeClock) -> str:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    return bootstrap_identity(database, settings, clock, _ids()).user_id


def _webhook_settings(settings: AppSettings) -> AppSettings:
    return settings.model_copy(
        update={
            "qq_webhook_token": SecretStr("test-webhook-token"),
            "qq_user_openid": "openid-1",
        }
    )


def test_webhook_accepts_once_and_rejects_invalid_token(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    effective = _webhook_settings(settings)
    assert effective.qq_webhook_token is not None
    webhook_token = effective.qq_webhook_token.get_secret_value()
    qq = FakeQQGateway(inbound_token=webhook_token)
    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )
    payload = {
        "event_id": "event-1",
        "message_id": "message-1",
        "user_openid": "openid-1",
        "content": "腾讯现在什么状态",
    }
    with TestClient(app) as client:
        assert client.post("/webhooks/qq", json=payload).status_code == 401
        first = client.post(
            "/webhooks/qq", json=payload, headers={"x-qq-webhook-token": webhook_token}
        )
        duplicate = client.post(
            "/webhooks/qq", json=payload, headers={"x-qq-webhook-token": webhook_token}
        )
    assert first.status_code == 202, first.text
    assert duplicate.status_code == 200
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar_one() == 1


def test_registry_exposes_phase_five_tools_with_confirmation_gated_writes() -> None:
    assert tuple(definition.name for definition in definitions()) == (
        *TOOL_NAMES,
        *WRITE_TOOL_NAMES,
    )
    assert sum(definition.permission.value == "WRITE" for definition in definitions()) == 3
    write_definition = next(
        definition for definition in definitions() if definition.name == "UpdateApplicationStatus"
    )
    assert write_definition.requires_confirmation is True


def test_runtime_rejects_ninth_tool_and_forbidden_write(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_application_id, active_mail_id, "
                "active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, NULL, :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages VALUES "
                "('message', 'session', 'user', 'query', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, final_message_id, state, error, "
                "delivery_state, delivery_error, provider_message_id, "
                "created_at, completed_at) "
                "VALUES ('run', 'session', 'message', NULL, 'RUNNING', NULL, "
                "NULL, NULL, NULL, :now, NULL)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    llm = FakeLLM(
        conversation_responses=[
            *[
                ConversationResponse(tool_call=ToolCallRequest(name="SearchApplications"))
                for _ in range(9)
            ],
        ]
    )
    process_run(RuntimeServices(database, llm, FakeQQGateway(), fake_clock, ids), "run")
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM tool_calls")).scalar_one() == 8
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "FAILED"

    with database.engine.begin() as connection:
        connection.execute(
            text("UPDATE agent_runs SET state='RUNNING', error=NULL, completed_at=NULL")
        )
        connection.execute(text("DELETE FROM tool_results"))
        connection.execute(text("DELETE FROM tool_calls"))
    write = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={
                        "company": "腾讯",
                        "position": "后端",
                        "status": "APPLIED",
                    },
                )
            )
        ]
    )
    process_run(RuntimeServices(database, write, FakeQQGateway(), fake_clock, ids), "run")
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == (
            AgentRunState.WAITING_USER_CONFIRMATION.value
        )
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0

    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM tool_results"))
        connection.execute(text("DELETE FROM tool_calls"))
        connection.execute(
            text("UPDATE agent_runs SET state='RUNNING', error=NULL, completed_at=NULL")
        )
    malformed = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={"company": "腾讯", "position": "后端", "status": "INTERVIEW"},
                )
            )
        ]
    )
    process_run(RuntimeServices(database, malformed, FakeQQGateway(), fake_clock, ids), "run")
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "FAILED"
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 1
        assert (
            "malformed arguments"
            in connection.execute(text("SELECT error FROM tool_results")).scalar_one()
        )


def test_write_proposal_freezes_arguments_and_confirmation_executes_once(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_application_id, active_mail_id, "
                "active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', '腾讯已经入职了', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={
                        "company": "腾讯",
                        "position": "后端",
                        "status": "APPLIED",
                    },
                )
            )
        ]
    )
    process_run(RuntimeServices(database, llm, FakeQQGateway(), fake_clock, ids), "run")
    with database.engine.connect() as connection:
        action = connection.execute(
            text("SELECT id, confirmation_code, resolved_arguments, state FROM pending_actions")
        ).one()
        assert action.state == "PENDING"
        assert '"status": "APPLIED"' in action.resolved_arguments
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0


def test_confirmation_event_is_durable_deterministic_and_resumes_original_run(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    proposal_llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={
                        "company": "腾讯",
                        "position": "后端",
                        "status": "APPLIED",
                    },
                )
            )
        ]
    )
    process_run(RuntimeServices(database, proposal_llm, FakeQQGateway(), fake_clock, ids), "run")
    with database.engine.connect() as connection:
        code = connection.execute(
            text("SELECT confirmation_code FROM pending_actions")
        ).scalar_one()
    response_llm = FakeLLM(conversation_responses=[ConversationResponse(answer="已更新")])
    action = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text=f"确认 {code}",
        clock=fake_clock,
        ids=ids,
    )
    assert action is not None
    resume_confirmed_run(
        RuntimeServices(database, response_llm, FakeQQGateway(), fake_clock, ids),
        action.action_id,
    )
    assert response_llm.calls == [("converse", ("update",))]
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT state FROM pending_actions")).scalar_one()
            == "COMPLETED"
        )
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "COMPLETED"
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM tool_results")).scalar_one() == 1


def test_rejection_bypasses_llm_and_leaves_facts_unchanged(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'WAITING_USER_CONFIRMATION', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO pending_actions "
                "(id, user_id, session_id, agent_run_id, tool_call_id, source_type, source_id, "
                "action_type, resolved_arguments, display_summary, proposal_fingerprint, "
                "confirmation_code, state, created_at, expires_at, attempt_count) "
                "VALUES ('action', :user, 'session', 'run', 'call', 'agent_tool', 'session', "
                "'UpdateApplicationStatus', :arguments, 'summary', 'fingerprint', 'PA-ABCD', "
                "'PENDING', "
                ":now, :expires, 0)"
            ),
            {
                "user": user_id,
                "arguments": '{"company":"腾讯","position":"后端","status":"APPLIED"}',
                "now": fake_clock.now().isoformat(),
                "expires": "2026-01-08T00:00:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO tool_calls (id, agent_run_id, tool_name, arguments, sequence, "
                "created_at) "
                "VALUES ('call', 'run', 'UpdateApplicationStatus', :arguments, 1, :now)"
            ),
            {
                "arguments": '{"company":"腾讯","position":"后端","status":"APPLIED"}',
                "now": fake_clock.now().isoformat(),
            },
        )
    llm = FakeLLM(conversation_responses=[ConversationResponse(answer="must not run")])
    action = resolve_confirmation(
        database,
        user_id=UserId(user_id),
        command_text="拒绝 PA-ABCD",
        clock=fake_clock,
        ids=ids,
    )
    assert action is not None
    run_id = finalize_rejected_action(database, action.action_id, fake_clock, ids)
    assert run_id == "run"
    assert llm.calls == []
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT state FROM pending_actions")).scalar_one() == "REJECTED"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0


def test_invalid_confirmation_is_durable_without_business_mutation(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    effective = _webhook_settings(settings)
    assert effective.qq_webhook_token is not None
    webhook_token = effective.qq_webhook_token.get_secret_value()
    qq = FakeQQGateway(inbound_token=webhook_token)
    app = create_app(
        effective, Path(__file__).resolve().parents[2], LifecycleAdapters(qq=qq, clock=fake_clock)
    )
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/qq",
            json={
                "event_id": "invalid-event",
                "message_id": "invalid-message",
                "user_openid": "openid-1",
                "content": "确认 PA-ZZZZ",
            },
            headers={"x-qq-webhook-token": webhook_token},
        )
    assert response.status_code == 202
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0


def test_confirmed_action_recovery_resumes_original_run_after_restart(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'WAITING_USER_CONFIRMATION', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tool_calls (id, agent_run_id, tool_name, arguments, sequence, "
                "created_at) VALUES ('call', 'run', 'UpdateApplicationStatus', :arguments, 1, :now)"
            ),
            {
                "arguments": '{"company":"腾讯","position":"后端","status":"APPLIED"}',
                "now": fake_clock.now().isoformat(),
            },
        )
    with transaction(database) as session:
        action = create_status_proposal(
            session,
            user_id=UserId(user_id),
            source_id="session",
            arguments=StatusUpdateArguments(
                company="腾讯",
                position="后端",
                status=ApplicationStatus.APPLIED,
            ),
            clock=fake_clock,
            ids=ids,
            source_type="agent_tool",
        )
        action.session_id = "session"
        action.agent_run_id = "run"
        action.tool_call_id = "call"
        action.state = "CONFIRMED"
        action.state = "CONFIRMED"
    recovered = recover_pending_actions(database, clock=fake_clock, ids=ids)
    assert recovered[0].state.value == "COMPLETED"
    response_llm = FakeLLM(conversation_responses=[ConversationResponse(answer="恢复完成")])
    resume_confirmed_run(
        RuntimeServices(database, response_llm, FakeQQGateway(), fake_clock, ids),
        action.id,
    )
    assert response_llm.calls == [("converse", ("update",))]
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "COMPLETED"


def test_pending_write_tool_call_recovers_without_llm_and_delivers_once(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO tool_calls "
                "(id, agent_run_id, tool_name, arguments, sequence, created_at) "
                "VALUES ('call', 'run', 'UpdateApplicationStatus', :arguments, 1, :now)"
            ),
            {
                "arguments": '{"company":"腾讯","position":"后端","status":"APPLIED"}',
                "now": fake_clock.now().isoformat(),
            },
        )
    llm = FakeLLM(conversation_responses=[])
    qq = FakeQQGateway()

    process_run(RuntimeServices(database, llm, qq, fake_clock, ids), "run")
    process_run(RuntimeServices(database, llm, qq, fake_clock, ids), "run")

    assert llm.calls == []
    assert [call[0] for call in qq.calls].count("push") == 1
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM pending_actions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM conversation_messages WHERE role='assistant'")
            ).scalar_one()
            == 1
        )


def test_confirmation_prompt_delivery_failure_is_persisted_and_retried(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, delivery_state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', NULL, :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={"company": "腾讯", "position": "后端", "status": "APPLIED"},
                )
            )
        ]
    )
    failing_qq = FakeQQGateway(error=RuntimeError("provider unavailable"))
    process_run(RuntimeServices(database, llm, failing_qq, fake_clock, ids), "run")
    with database.engine.connect() as connection:
        run = connection.execute(
            text("SELECT state, delivery_state, final_message_id FROM agent_runs")
        ).one()
        assert run.state == "WAITING_USER_CONFIRMATION"
        assert run.delivery_state == "FAILED"
        confirmation_prompt = connection.execute(
            text("SELECT content FROM conversation_messages WHERE id=:message_id"),
            {"message_id": run.final_message_id},
        ).scalar_one()
    fake_clock.advance(1)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('later', 'session', 'assistant', 'unrelated', NULL, :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )

    retry_qq = FakeQQGateway()
    _agent_cycle(
        database,
        LifecycleAdapters(llm=llm, qq=retry_qq, clock=fake_clock, ids=ids),
        settings,
    )
    assert retry_qq.calls[0][0] == "push"
    assert retry_qq.calls[0][1][1] == confirmation_prompt
    with database.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT delivery_state FROM agent_runs")).scalar_one() == "SENT"
        )


def test_terminal_action_reconciliation_is_idempotent(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_application_id, "
                "active_mail_id, active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'update', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'WAITING_USER_CONFIRMATION', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    with transaction(database) as session:
        action = create_status_proposal(
            session,
            user_id=UserId(user_id),
            source_id="call",
            arguments=StatusUpdateArguments(
                company="腾讯", position="后端", status=ApplicationStatus.APPLIED
            ),
            clock=fake_clock,
            ids=ids,
            source_type="agent_tool",
        )
        action.session_id = "session"
        action.agent_run_id = "run"
        action.tool_call_id = "call"
        action.state = "CONFIRMED"
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tool_calls "
                "(id, agent_run_id, tool_name, arguments, sequence, created_at) "
                "VALUES ('call', 'run', 'UpdateApplicationStatus', :arguments, 1, :now)"
            ),
            {
                "arguments": '{"company":"腾讯","position":"后端","status":"APPLIED"}',
                "now": fake_clock.now().isoformat(),
            },
        )
    execute_status_update(database, action_id=action.id, clock=fake_clock, ids=ids)
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE agent_runs SET state='WAITING_USER_CONFIRMATION'"))
    services = RuntimeServices(database, FakeLLM(), FakeQQGateway(), fake_clock, ids)
    assert reconcile_terminal_actions(services) == ("run",)
    assert reconcile_terminal_actions(services) == ()
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "RUNNING"


def test_same_session_concurrency_and_recovery(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    _setup(database, settings, fake_clock)
    effective = _webhook_settings(settings)
    assert effective.qq_webhook_token is not None
    webhook_token = effective.qq_webhook_token.get_secret_value()
    qq = FakeQQGateway(inbound_token=webhook_token)
    app = create_app(
        effective,
        Path(__file__).resolve().parents[2],
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )
    with TestClient(app) as client:
        for event_id in ("event-1", "event-2"):
            response = client.post(
                "/webhooks/qq",
                json={
                    "event_id": event_id,
                    "message_id": event_id,
                    "user_openid": "openid-1",
                    "content": "query",
                },
                headers={"x-qq-webhook-token": webhook_token},
            )
            assert response.status_code == 202
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM agent_runs WHERE state='RUNNING'")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM agent_runs WHERE state='QUEUED'")
            ).scalar_one()
            == 1
        )
    fake_clock.advance(121)
    assert recover_runs(database, fake_clock) == 1
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM agent_runs WHERE state='RUNNING'")
            ).scalar_one()
            == 0
        )


def test_run_claim_persists_started_at_and_attempt_count(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', 'run', :now, :now, :now)"
            ),
            {"user": "00000000-0000-0000-0000-000000000001", "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages (id, session_id, role, content, created_at) "
                "VALUES ('message', 'session', 'user', 'query', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="ok")]),
            FakeQQGateway(),
            fake_clock,
            ids,
        ),
        "run",
    )

    with database.engine.connect() as connection:
        row = connection.execute(text("SELECT started_at, attempt_count FROM agent_runs")).one()
        assert row.started_at is not None
        assert row.attempt_count == 1


def test_waiting_confirmation_run_is_not_stale_recovered(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    user_id = _setup(database, settings, fake_clock)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages (id, session_id, role, content, created_at) "
                "VALUES ('message', 'session', 'user', 'query', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'WAITING_USER_CONFIRMATION', :old)"
            ),
            {"old": (fake_clock.now() - timedelta(minutes=10)).isoformat()},
        )

    assert recover_runs(database, fake_clock) == 0


def test_tool_timeout_and_result_limit_are_durable(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_application_id, active_mail_id, "
                "active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'query', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )

    def slow_tool(*_: str | int | bool | None) -> ToolExecution:
        sleep(0.05)
        return ToolExecution("slow", {})

    slow_llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(tool_call=ToolCallRequest(name="SearchApplications")),
        ]
    )
    process_run(
        RuntimeServices(
            database,
            slow_llm,
            FakeQQGateway(),
            fake_clock,
            ids,
            tool_timeout_seconds=0.001,
            tool_executor=slow_tool,
        ),
        "run",
    )
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "FAILED"
        assert (
            "time limit" in connection.execute(text("SELECT error FROM tool_results")).scalar_one()
        )

    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM tool_results"))
        connection.execute(text("DELETE FROM tool_calls"))
        connection.execute(
            text("UPDATE agent_runs SET state='RUNNING', error=NULL, completed_at=NULL")
        )
    oversized_llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(tool_call=ToolCallRequest(name="SearchApplications")),
        ]
    )
    process_run(
        RuntimeServices(
            database,
            oversized_llm,
            FakeQQGateway(),
            fake_clock,
            ids,
            max_result_chars=1,
            tool_executor=lambda *_: ToolExecution("too long", {}),
        ),
        "run",
    )
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "FAILED"
        assert (
            "size limit" in connection.execute(text("SELECT error FROM tool_results")).scalar_one()
        )


def test_qq_delivery_failure_is_persisted(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    ids = _ids()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_application_id, active_mail_id, "
                "active_run_id, created_at, last_active_at, updated_at) "
                "VALUES ('session', :user, 'MAIN', '', NULL, NULL, 'run', :now, :now, :now)"
            ),
            {"user": user_id, "now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'query', 'event', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', 'RUNNING', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    llm = FakeLLM(conversation_responses=[ConversationResponse(answer="answer")])
    qq = FakeQQGateway(error=RuntimeError("provider unavailable"))
    process_run(RuntimeServices(database, llm, qq, fake_clock, ids), "run")
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "COMPLETED"
        assert (
            connection.execute(text("SELECT delivery_state FROM agent_runs")).scalar_one()
            == "FAILED"
        )
        assert connection.execute(
            text("SELECT delivery_error FROM agent_runs")
        ).scalar_one() == safe_external_error(RuntimeError("provider unavailable"))
