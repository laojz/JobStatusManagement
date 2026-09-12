"""Phase 3 durable conversation agent integration tests."""

from datetime import timedelta
from pathlib import Path
from time import sleep
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from starlette.testclient import TestClient

from jobs_status_manager.agent.contracts import (
    AgentRunState,
    ConversationResponse,
    ProviderError,
    ProviderErrorKind,
    QQInboundEvent,
    ReplyMode,
    ReplyTarget,
    ToolCallRequest,
)
from jobs_status_manager.agent.runtime import (
    RuntimeServices,
    _prepare_run_attempt,
    process_run,
    recover_runs,
    resume_confirmed_run,
)
from jobs_status_manager.agent.tools import TOOL_NAMES, WRITE_TOOL_NAMES, definitions
from jobs_status_manager.agent.webhook import WebhookContext, _persist_event
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
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeLLM,
    FakeQQDeliveryResult,
    FakeQQGateway,
)
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


def _seed_runtime_run(
    database: Database,
    user_id: str,
    now: str,
    *,
    provider_metadata: bool,
) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) VALUES "
                "('runtime-session', :user_id, 'MAIN', '', 'runtime-run', :now, :now, :now)"
            ),
            {"user_id": user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, provider_name, "
                "provider_scope, provider_target_id, provider_message_id, provider_msg_seq, "
                "created_at) VALUES ('runtime-message', 'runtime-session', 'user', 'hello', "
                ":provider_event_id, :provider_name, :provider_scope, :provider_target_id, "
                ":provider_message_id, :provider_msg_seq, :now)"
            ),
            {
                "now": now,
                "provider_event_id": "event" if provider_metadata else None,
                "provider_name": "qq" if provider_metadata else None,
                "provider_scope": "c2c" if provider_metadata else None,
                "provider_target_id": "openid" if provider_metadata else None,
                "provider_message_id": "message" if provider_metadata else None,
                "provider_msg_seq": 4 if provider_metadata else None,
            },
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('runtime-run', 'runtime-session', 'runtime-message', 'RUNNING', :now)"
            ),
            {"now": now},
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


def test_baseline_answer_completion_persists_terminal_delivery_state(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    with database.engine.begin() as connection:
        now = fake_clock.now().isoformat()
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) VALUES "
                "('baseline-session', :user_id, 'MAIN', '', 'baseline-run', :now, :now, :now)"
            ),
            {"user_id": user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) VALUES "
                "('baseline-message', 'baseline-session', 'user', 'hello', 'baseline-event', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('baseline-run', 'baseline-session', 'baseline-message', 'RUNNING', :now)"
            ),
            {"now": now},
        )

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            FakeQQGateway(),
            fake_clock,
            _ids(),
        ),
        "baseline-run",
    )

    with database.engine.connect() as connection:
        state = connection.execute(text("SELECT state FROM agent_runs")).scalar_one()
        delivery_state = connection.execute(
            text("SELECT delivery_state FROM agent_runs")
        ).scalar_one()
    assert state == AgentRunState.COMPLETED.value
    assert delivery_state == "SENT"


def test_runtime_delivers_final_answer_to_persisted_passive_target(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=True)
    qq = FakeQQGateway()

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            qq,
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    assert qq.calls[0][0] == "deliver"
    assert qq.calls[0][1][:7] == ("PASSIVE", "qq", "c2c", "openid", "message", "event", "4")
    with database.engine.connect() as connection:
        provider_message_id = connection.execute(
            text("SELECT provider_message_id FROM agent_runs")
        ).scalar_one()
    assert provider_message_id == "fake-message"


def test_runtime_uses_only_explicit_proactive_target_without_passive_metadata(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=False)
    qq = FakeQQGateway()
    proactive = ReplyTarget(ReplyMode.PROACTIVE, "qq", "c2c", "configured-openid")

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="主动答复")]),
            qq,
            fake_clock,
            _ids(),
            proactive_target=proactive,
        ),
        "runtime-run",
    )

    assert qq.calls[0][0] == "deliver"
    assert qq.calls[0][1][:7] == ("PROACTIVE", "qq", "c2c", "configured-openid", "", "", "")


@pytest.mark.parametrize(
    "case",
    [
        (ProviderErrorKind.RETRYABLE, AgentRunState.DELIVERY_PENDING.value, "RETRY_WAIT"),
        (ProviderErrorKind.PERMANENT, AgentRunState.FAILED.value, "FAILED"),
        (ProviderErrorKind.AMBIGUOUS, AgentRunState.FAILED.value, "AMBIGUOUS"),
    ],
)
def test_runtime_persists_provider_delivery_classification(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    case: tuple[ProviderErrorKind, str, str],
) -> None:
    kind, run_state, delivery_state = case
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=True)
    qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(kind=kind, provider_name="qq", code="test"),
        )
    )

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            qq,
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    with database.engine.connect() as connection:
        row = connection.execute(
            text("SELECT state, delivery_state, next_retry_at FROM agent_runs")
        ).one()
    assert row.state == run_state
    assert row.delivery_state == delivery_state
    assert (row.next_retry_at is not None) is (kind is ProviderErrorKind.RETRYABLE)


def test_retryable_ordinary_delivery_keeps_session_claim_before_queued_run(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=False)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, created_at) "
                "VALUES ('queued-message', 'runtime-session', 'user', 'queued', :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, attempt_count, created_at) "
                "VALUES ('queued-run', 'runtime-session', 'queued-message', 'QUEUED', 0, :now)"
            ),
            {"now": fake_clock.now().isoformat()},
        )
    qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(
                kind=ProviderErrorKind.RETRYABLE,
                provider_name="qq",
                code="timeout",
            ),
        )
    )

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            qq,
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    with database.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT sessions.active_run_id, current.state, current.delivery_state, "
                "queued.state AS queued_state FROM sessions "
                "JOIN agent_runs AS current ON current.id='runtime-run' "
                "JOIN agent_runs AS queued ON queued.id='queued-run' "
                "WHERE sessions.id='runtime-session'"
            )
        ).one()
    assert rows.active_run_id == "runtime-run"
    assert rows.state == AgentRunState.DELIVERY_PENDING.value
    assert rows.delivery_state == "RETRY_WAIT"
    assert rows.queued_state == AgentRunState.QUEUED.value


def test_exception_based_ordinary_delivery_is_ambiguous_not_completed(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=False)

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            FakeQQGateway(error=RuntimeError("provider unavailable")),
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    with database.engine.connect() as connection:
        row = connection.execute(text("SELECT state, delivery_state FROM agent_runs")).one()
    assert row.state == AgentRunState.FAILED.value
    assert row.delivery_state == "AMBIGUOUS"


def test_second_inbound_after_retryable_delivery_remains_queued(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=False)
    qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_error=ProviderError(
                kind=ProviderErrorKind.RETRYABLE,
                provider_name="qq",
                code="timeout",
            ),
        )
    )

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            qq,
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    webhook_settings = _webhook_settings(settings).model_copy(
        update={"bootstrap_user_external_key": settings.bootstrap_user_external_key}
    )
    second_ids = DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(100, 120)]
    )
    response = _persist_event(
        WebhookContext(database, webhook_settings, fake_clock, second_ids),
        QQInboundEvent(
            event_id="second-event",
            user_openid="openid-1",
            message_id="second-message",
            event_type="C2C_MESSAGE_CREATE",
            content="second inbound",
        ),
    )

    assert response.status_code == 202
    with database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT sessions.active_run_id, agent_runs.state "
                "FROM sessions JOIN agent_runs ON agent_runs.id='runtime-run' "
                "WHERE sessions.id='runtime-session'"
            )
        ).one()
        queued_state = connection.execute(
            text(
                "SELECT state FROM agent_runs WHERE user_message_id = "
                "(SELECT id FROM conversation_messages WHERE provider_event_id='second-event')"
            )
        ).scalar_one()
    assert row.active_run_id == "runtime-run"
    assert row.state == AgentRunState.DELIVERY_PENDING.value
    assert queued_state == AgentRunState.QUEUED.value


def test_exception_based_oserror_delivery_is_ambiguous_not_completed(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    _seed_runtime_run(database, user_id, fake_clock.now().isoformat(), provider_metadata=False)

    process_run(
        RuntimeServices(
            database,
            FakeLLM(conversation_responses=[ConversationResponse(answer="答复")]),
            FakeQQGateway(error=OSError("provider unavailable")),
            fake_clock,
            _ids(),
        ),
        "runtime-run",
    )

    with database.engine.connect() as connection:
        row = connection.execute(text("SELECT state, delivery_state FROM agent_runs")).one()
    assert row.state == AgentRunState.FAILED.value
    assert row.delivery_state == "AMBIGUOUS"


@pytest.mark.parametrize(
    "case",
    [
        ("future", 1, False, AgentRunState.DELIVERY_PENDING.value),
        ("due", 1, True, AgentRunState.DELIVERY_PENDING.value),
        ("due", 3, False, AgentRunState.FAILED.value),
    ],
)
def test_sqlite_delivery_retry_preparation_handles_persisted_naive_times(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
    case: tuple[str, int, bool, str],
) -> None:
    retry_at, attempt_count, expected_claim, expected_state = case
    user_id = _setup(database, settings, fake_clock)
    now = fake_clock.now()
    persisted_retry_at = (
        now + timedelta(minutes=5) if retry_at == "future" else now - timedelta(minutes=1)
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) VALUES "
                "('retry-session', :user_id, 'MAIN', '', 'retry-run', :now, :now, :now)"
            ),
            {"user_id": user_id, "now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, created_at) VALUES "
                "('retry-message', 'retry-session', 'user', 'retry', :now)"
            ),
            {"now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, attempt_count, next_retry_at, "
                "created_at) "
                "VALUES ('retry-run', 'retry-session', 'retry-message', 'DELIVERY_PENDING', "
                ":attempt_count, :next_retry_at, :now)"
            ),
            {
                "attempt_count": attempt_count,
                "next_retry_at": persisted_retry_at.replace(tzinfo=None).isoformat(),
                "now": now.isoformat(),
            },
        )

    services = RuntimeServices(
        database,
        FakeLLM(),
        FakeQQGateway(),
        fake_clock,
        _ids(),
    )
    claimed = _prepare_run_attempt(services, "retry-run")

    assert claimed is expected_claim
    with database.engine.connect() as connection:
        row = connection.execute(
            text("SELECT state, attempt_count, next_retry_at FROM agent_runs")
        ).one()
    assert row.state == expected_state
    assert row.attempt_count == attempt_count + int(expected_claim)
    assert (row.next_retry_at is None) is (
        expected_claim or expected_state == AgentRunState.FAILED.value
    )


def test_sqlite_retry_exhaustion_releases_session_and_promotes_queued_successor(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    now = fake_clock.now()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) VALUES "
                "('exhaust-session', :user_id, 'MAIN', '', 'exhaust-run', :now, :now, :now)"
            ),
            {"user_id": user_id, "now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, created_at) VALUES "
                "('exhaust-message', 'exhaust-session', 'user', 'retry', :now), "
                "('successor-message', 'exhaust-session', 'user', 'next', :now)"
            ),
            {"now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, state, attempt_count, next_retry_at, "
                "created_at) "
                "VALUES ('exhaust-run', 'exhaust-session', 'exhaust-message', "
                "'DELIVERY_PENDING', 3, :now, :now), "
                "('successor-run', 'exhaust-session', 'successor-message', 'QUEUED', 0, NULL, :now)"
            ),
            {"now": now.isoformat()},
        )

    services = RuntimeServices(
        database,
        FakeLLM(),
        FakeQQGateway(),
        fake_clock,
        _ids(),
    )
    claimed = _prepare_run_attempt(services, "exhaust-run")

    assert claimed is False
    with database.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, state, attempt_count FROM agent_runs ORDER BY created_at, id")
        ).all()
        active_run_id = connection.execute(
            text("SELECT active_run_id FROM sessions WHERE id='exhaust-session'")
        ).scalar_one()
    assert rows[0].state == AgentRunState.FAILED.value
    assert rows[1].state == AgentRunState.RUNNING.value
    assert rows[1].attempt_count == 1
    assert active_run_id == "successor-run"


def test_command_delivery_retry_preserves_confirmation_claim_after_sqlite_restart(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    user_id = _setup(database, settings, fake_clock)
    now = fake_clock.now()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, active_run_id, "
                "created_at, last_active_at, updated_at) VALUES "
                "('command-session', :user_id, 'MAIN', '', 'command-run', :now, :now, :now)"
            ),
            {"user_id": user_id, "now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, created_at) VALUES "
                "('command-message', 'command-session', 'user', '确认 PA-ABCD', :now), "
                "('command-answer', 'command-session', 'assistant', '已确认; 正在处理.', :now)"
            ),
            {"now": now.isoformat()},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, session_id, user_message_id, final_message_id, state, delivery_state, "
                "attempt_count, next_retry_at, created_at) VALUES "
                "('command-run', 'command-session', 'command-message', 'command-answer', "
                "'DELIVERY_PENDING', 'RETRY_WAIT', 1, :next_retry_at, :now)"
            ),
            {"next_retry_at": (now - timedelta(minutes=1)).isoformat(), "now": now.isoformat()},
        )

    database.dispose()
    restarted_database = Database(settings.database_path)
    qq = FakeQQGateway(
        delivery=FakeQQDeliveryResult(
            success=False,
            provider_message_id=None,
            provider_error=ProviderError(
                kind=ProviderErrorKind.RETRYABLE,
                provider_name="qq",
                code="retry",
            ),
        )
    )
    try:
        process_run(
            RuntimeServices(restarted_database, FakeLLM(), qq, fake_clock, _ids()),
            "command-run",
        )

        with restarted_database.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT sessions.active_run_id, agent_runs.state, agent_runs.delivery_state "
                    "FROM sessions JOIN agent_runs ON agent_runs.session_id = sessions.id"
                )
            ).one()
    finally:
        restarted_database.dispose()
    assert row.active_run_id == "command-run"
    assert row.state == AgentRunState.DELIVERY_PENDING.value
    assert row.delivery_state == "RETRY_WAIT"


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
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'query', 'event', :now)"
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
        assert connection.execute(text("SELECT state FROM agent_runs")).scalar_one() == "FAILED"
        assert (
            connection.execute(text("SELECT delivery_state FROM agent_runs")).scalar_one()
            == "AMBIGUOUS"
        )
        assert connection.execute(
            text("SELECT delivery_error FROM agent_runs")
        ).scalar_one() == safe_external_error(RuntimeError("provider unavailable"))
