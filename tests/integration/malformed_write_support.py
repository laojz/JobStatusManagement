import json
from pathlib import Path

from sqlalchemy import text

from jobs_status_manager.agent.contracts import ToolCallRequest
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.ids import UUIDGenerator


def write_request(
    name: str,
    arguments: dict[str, str | int | bool | None],
) -> ToolCallRequest:
    return ToolCallRequest(
        provider_call_id=f"{name}-call",
        provider_type="function",
        name=name,
        arguments=arguments,
        arguments_json=json.dumps(arguments, separators=(",", ":")),
    )


def seed_run(database: Database, settings: AppSettings, clock: FakeClock) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    identity = bootstrap_identity(database, settings, clock, UUIDGenerator())
    now = clock.now().isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_run_id, created_at, "
                "last_active_at, updated_at) VALUES "
                "('malformed-session', :user_id, 'MAIN', '', 'malformed-run', :now, :now, :now)"
            ),
            {"user_id": identity.user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, provider_name, "
                "provider_scope, provider_target_id, provider_message_id, provider_msg_seq, "
                "created_at) VALUES "
                "('malformed-message', 'malformed-session', 'user', '新增小米求职状态', "
                "'malformed-event', 'qq', 'c2c', 'openid-1', 'qq-message-1', 1, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('malformed-run', 'malformed-session', 'malformed-message', "
                "'RUNNING', :now)"
            ),
            {"now": now},
        )
