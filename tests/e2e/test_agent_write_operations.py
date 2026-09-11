"""Phase 4 write-operation HTTP scenario."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import SecretStr
from sqlalchemy import text
from starlette.testclient import TestClient

from jobs_status_manager.agent.contracts import ConversationResponse, ToolCallRequest
from jobs_status_manager.application.lifecycle import LifecycleAdapters, _agent_cycle, create_app
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.infrastructure.adapters.fakes import FakeLLM, FakeQQGateway
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.ids import UUIDGenerator

if TYPE_CHECKING:
    from jobs_status_manager.config.settings import AppSettings
    from jobs_status_manager.infrastructure.clock import FakeClock
    from jobs_status_manager.infrastructure.database.connection import Database


def test_qq_status_update_requires_confirmation_and_resumes_original_run(
    database: Database,
    settings: AppSettings,
    fake_clock: FakeClock,
) -> None:
    root = Path(__file__).resolve().parents[2]
    upgrade_database(root, f"sqlite:///{settings.database_path}")
    bootstrap_identity(database, settings, fake_clock, UUIDGenerator())
    effective = settings.model_copy(
        update={
            "qq_webhook_token": SecretStr("phase-4-token"),
            "qq_user_openid": "openid-1",
        }
    )
    assert effective.qq_webhook_token is not None
    webhook_token = effective.qq_webhook_token.get_secret_value()
    qq = FakeQQGateway(inbound_token=webhook_token)
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_call=ToolCallRequest(
                    name="UpdateApplicationStatus",
                    arguments={
                        "company": "腾讯",
                        "department": "后端",
                        "position": "校招",
                        "status": "INTERVIEW",
                        "interview_round": 3,
                    },
                )
            ),
            ConversationResponse(answer="腾讯校招状态已更新为三面。"),
        ]
    )
    app = create_app(
        effective,
        root,
        LifecycleAdapters(qq=qq, clock=fake_clock),
    )

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/qq",
            json={
                "event_id": "proposal-event",
                "message_id": "proposal-message",
                "user_openid": "openid-1",
                "content": "腾讯已经三面了",
            },
            headers={"x-qq-webhook-token": webhook_token},
        )
        assert response.status_code == 202
        _agent_cycle(
            database,
            LifecycleAdapters(llm=llm, qq=qq, clock=fake_clock),
            effective,
        )
        with database.engine.connect() as connection:
            action = connection.execute(
                text("SELECT confirmation_code, state FROM pending_actions")
            ).one()
            assert action.state == "PENDING"
            assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 0

        confirmation = client.post(
            "/webhooks/qq",
            json={
                "event_id": "confirmation-event",
                "message_id": "confirmation-message",
                "user_openid": "openid-1",
                "content": f"确认 {action.confirmation_code}",
            },
            headers={"x-qq-webhook-token": webhook_token},
        )
        assert confirmation.status_code == 202
        _agent_cycle(
            database,
            LifecycleAdapters(llm=llm, qq=qq, clock=fake_clock),
            effective,
        )

    assert [call[1][0] for call in llm.calls] == ["腾讯已经三面了", "腾讯已经三面了"]
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM applications")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM job_events")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT state FROM pending_actions")).scalar_one()
            == "COMPLETED"
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM outbox_events "
                    "WHERE event_type = 'APPLICATION_STATUS_CHANGED'"
                )
            ).scalar_one()
            == 1
        )
        result_refs = connection.execute(text("SELECT context_refs FROM tool_results")).scalar_one()
        assert '"resolution_state": "COMPLETED"' in result_refs
