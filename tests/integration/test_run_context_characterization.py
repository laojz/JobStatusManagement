import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from jobs_status_manager.agent.contracts import AgentRunState, QQInboundEvent
from jobs_status_manager.agent.runtime_support import load_context
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters.fakes import FakeQQGateway
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database


def test_load_context_preserves_legacy_run_shape(
    database: Database,
    settings: AppSettings,
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    now = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, external_key, display_name, created_at, updated_at) "
                "VALUES ('user', 'external', 'User', :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO sessions (id, user_id, session_type, summary, created_at, "
                "last_active_at, updated_at) VALUES "
                "('session', 'user', 'MAIN', '', :now, :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, created_at) "
                "VALUES ('message', 'session', 'user', 'hello', 'event', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('run', 'session', 'message', :state, :now)"
            ),
            {"state": AgentRunState.RUNNING.value, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO tool_calls "
                "(id, agent_run_id, tool_name, arguments, sequence, created_at) "
                "VALUES ('provider-call-1', 'run', 'GetRecentMails', '{}', 1, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO tool_results "
                "(id, tool_call_id, data, context_refs, error, started_at, completed_at, "
                "created_at) "
                "VALUES ('result-1', 'provider-call-1', 'data', '{}', NULL, :now, :now, :now)"
            ),
            {"now": now},
        )

    context = load_context(database, "run")

    assert context is not None
    assert context.user_message == "hello"
    assert context.state == AgentRunState.RUNNING.value
    assert context.tool_results[0].tool_call_id == "provider-call-1"


def test_fake_qq_gateway_records_existing_reply_and_push_shapes() -> None:
    gateway = FakeQQGateway()

    reply = gateway.reply("user", "message", "reply")
    push = gateway.push("user", "push")
    inbound_body = json.dumps(
        {
            "event_id": "event",
            "user_openid": "openid",
            "message_id": "message",
            "content": "hello",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    inbound = gateway.receive(inbound_body)
    received_body = inbound_body.decode("utf-8")

    assert reply.success is True
    assert push.provider_message_id == "fake-message"
    assert inbound == QQInboundEvent(
        event_id="event",
        user_openid="openid",
        message_id="message",
        content="hello",
    )
    assert gateway.calls == [
        ("reply", ("user", "message", "reply")),
        ("push", ("user", "push")),
        (
            "receive",
            (received_body,),
        ),
    ]
