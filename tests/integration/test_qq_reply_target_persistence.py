from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from jobs_status_manager.agent.contracts import AgentRunState, ReplyMode
from jobs_status_manager.agent.runtime_support import load_context, update_active_context
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database


def test_run_context_reconstructs_passive_target_after_restart(
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
                "(id, session_id, role, content, provider_event_id, provider_name, "
                "provider_scope, provider_target_id, provider_message_id, provider_msg_seq, "
                "created_at) "
                "VALUES ('message', 'session', 'user', 'hello', 'event', 'qq', 'c2c', "
                "'openid', 'message', 4, :now)"
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

    context = load_context(database, "run")

    assert context is not None
    assert context.reply_target is not None
    assert context.reply_target.mode is ReplyMode.PASSIVE
    assert context.reply_target.provider_name == "qq"
    assert context.reply_target.provider_scope == "c2c"
    assert context.reply_target.target_id == "openid"
    assert context.reply_target.message_id == "message"
    assert context.reply_target.event_id == "event"
    assert context.reply_target.msg_seq == 4


def test_context_updates_preserve_reply_target(
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
                "(id, session_id, role, content, provider_event_id, provider_name, "
                "provider_scope, provider_target_id, provider_message_id, created_at) "
                "VALUES ('message', 'session', 'user', 'hello', 'event', 'qq', 'c2c', "
                "'openid', 'message', :now)"
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

    context = load_context(database, "run")
    assert context is not None
    updated = update_active_context(database, context)

    assert updated.reply_target == context.reply_target
