import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from jobs_status_manager.agent.contracts import AgentRunState, ConversationResponse, ToolCallRequest
from jobs_status_manager.agent.models import ToolCall
from jobs_status_manager.agent.runtime import RuntimeServices, process_run
from jobs_status_manager.application_core.domain import UserId
from jobs_status_manager.application_core.service import resolve_confirmation
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.infrastructure.adapters._openai_compatible_llm_types import (
    LLMContractError,
)
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeChroma,
    FakeEmbedding,
    FakeLLM,
    FakeQQGateway,
)
from jobs_status_manager.infrastructure.clock import FakeClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.database.transactions import transaction
from jobs_status_manager.infrastructure.ids import DeterministicIdGenerator
from jobs_status_manager.knowledge.contracts import AddKnowledgeArguments, DocumentType, VectorHit
from jobs_status_manager.knowledge.index import IndexServices, index_once
from jobs_status_manager.knowledge.service import KnowledgeServices, execute_add, propose_add


def _ids(start: int) -> DeterministicIdGenerator:
    return DeterministicIdGenerator(
        [UUID(f"00000000-0000-0000-0000-{index:012d}") for index in range(start, start + 40)]
    )


def _seed_composite(
    database: Database, settings: AppSettings, clock: FakeClock
) -> tuple[str, str, FakeChroma]:
    root = Path(__file__).resolve().parents[2]
    setup_ids = _ids(1)
    upgrade_database(root, f"sqlite:///{settings.database_path}")

    identity = bootstrap_identity(database, settings, clock, setup_ids)
    now = clock.now().isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO user_files "
                "(id, user_id, provider_file_id, filename, content_type, size_bytes, state, "
                "created_at, updated_at) VALUES "
                "('source-file', :user_id, 'provider-file-1', 'notes.txt', 'text/plain', 32, "
                "'STORED', :now, :now)"
            ),
            {"user_id": identity.user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO applications "
                "(id, user_id, company, department, position, company_key, department_key, "
                "position_key, current_status, current_interview_round, created_at, updated_at) "
                "VALUES ('application-1', :user_id, 'Example Corp', 'Platform', 'Backend', "
                "'example corp', 'platform', 'backend', 'APPLIED', NULL, :now, :now)"
            ),
            {"user_id": identity.user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO mails "
                "(id, user_id, mail_account_id, provider_message_id, subject, sender, recipients, "
                "received_at, content, processing_state, attempt_count, created_at, updated_at) "
                "VALUES ('mail-1', :user_id, :account_id, 'provider-mail-1', 'Interview update', "
                "'recruiter@example.com', '[\"test@example.com\"]', :now, 'Interview details', "
                "'SUCCEEDED', 1, :now, :now)"
            ),
            {"user_id": identity.user_id, "account_id": identity.mail_account_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO sessions "
                "(id, user_id, session_type, summary, active_run_id, created_at, "
                "last_active_at, updated_at) "
                "VALUES ('composite-session', :user_id, 'MAIN', '', 'composite-run', "
                ":now, :now, :now)"
            ),
            {"user_id": identity.user_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, session_id, role, content, provider_event_id, provider_name, "
                "provider_scope, provider_target_id, provider_message_id, provider_msg_seq, "
                "created_at) "
                "VALUES ('composite-message', 'composite-session', 'user', '请综合查询', "
                "'local-composite-event', 'local', 'conversation', 'user-1', 'message-1', 1, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO agent_runs (id, session_id, user_message_id, state, created_at) "
                "VALUES ('composite-run', 'composite-session', 'composite-message', "
                "'RUNNING', :now)"
            ),
            {"now": now},
        )
    knowledge = propose_add(
        KnowledgeServices(database, clock, setup_ids),
        identity.user_id,
        "seed-knowledge-call",
        AddKnowledgeArguments(
            source_file_id="source-file",
            title="Distributed systems notes",
            document_type=DocumentType.INTERVIEW_KNOWLEDGE,
            content="Distributed systems interview notes",
            tags=("distributed", "systems"),
        ),
    )
    resolved = resolve_confirmation(
        database,
        user_id=UserId(identity.user_id),
        command_text=f"确认 {knowledge.confirmation_code}",
        clock=clock,
        ids=setup_ids,
    )
    assert resolved is not None
    document = execute_add(KnowledgeServices(database, clock, setup_ids), knowledge.id)
    assert document.document_id is not None
    chroma = FakeChroma()
    index_services = IndexServices(database, clock, FakeEmbedding(vector=[0.2, 0.4]), chroma)
    assert index_once(index_services) == document.document_id
    with database.engine.connect() as connection:
        chunk_id = connection.execute(
            text("SELECT id FROM knowledge_chunks WHERE document_id = :document_id"),
            {"document_id": document.document_id},
        ).scalar_one()
    chroma.hits.append(VectorHit(chunk_id, 0.1))
    return identity.user_id, document.document_id, chroma


def _services(
    database: Database,
    clock: FakeClock,
    llm: FakeLLM,
    qq: FakeQQGateway,
    chroma: FakeChroma,
) -> RuntimeServices:
    return RuntimeServices(
        database,
        llm,
        qq,
        clock,
        _ids(50),
        embedding=FakeEmbedding(vector=[0.2, 0.4]),
        chroma=chroma,
    )


def test_composite_agent_run_persists_ordered_results_and_replays_after_restart(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    user_id, document_id, chroma = _seed_composite(database, settings, fake_clock)
    with transaction(database) as session:
        session.add(
            ToolCall(
                id="replayed-application-call",
                agent_run_id="composite-run",
                tool_name="SearchApplications",
                arguments={"company": "Example Corp"},
                provider_call_id="provider-application-call",
                provider_type="function",
                provider_arguments_json='{"company":"Example Corp"}',
                assistant_sequence=1,
                sequence=1,
                created_at=fake_clock.now(),
            )
        )
    database.dispose()
    restarted = Database(settings.database_path)
    llm = FakeLLM(
        conversation_responses=[
            ConversationResponse(
                tool_calls=(
                    ToolCallRequest(
                        provider_call_id="provider-mail-call",
                        provider_type="function",
                        name="GetRecentMails",
                        arguments_json="{}",
                    ),
                    ToolCallRequest(
                        provider_call_id="provider-knowledge-call",
                        provider_type="function",
                        name="SearchKnowledge",
                        arguments={"query": "distributed systems"},
                        arguments_json='{"query":"distributed systems"}',
                    ),
                )
            ),
            ConversationResponse(answer="综合查询完成"),
        ]
    )
    qq = FakeQQGateway()
    try:
        process_run(
            _services(
                restarted,
                fake_clock,
                llm,
                qq,
                chroma,
            ),
            "composite-run",
        )
        with restarted.engine.connect() as connection:
            calls = connection.execute(
                text("SELECT id, tool_name, sequence FROM tool_calls ORDER BY sequence")
            ).all()
            results = connection.execute(
                text("SELECT tool_call_id, data, error FROM tool_results ORDER BY rowid")
            ).all()
            run = connection.execute(
                text("SELECT state, delivery_state FROM agent_runs WHERE id='composite-run'")
            ).one()
            context_refs = connection.execute(
                text(
                    "SELECT tool_results.context_refs FROM tool_results "
                    "JOIN tool_calls ON tool_calls.id = tool_results.tool_call_id "
                    "WHERE tool_calls.agent_run_id='composite-run' "
                    "AND tool_calls.tool_name='SearchKnowledge'"
                )
            ).scalar_one()
    finally:
        restarted.dispose()
    assert user_id
    assert [(row.tool_name, row.sequence) for row in calls] == [
        ("SearchApplications", 1),
        ("GetRecentMails", 2),
        ("SearchKnowledge", 3),
    ]
    assert [row.tool_call_id for row in results] == [
        "replayed-application-call",
        calls[1].id,
        calls[2].id,
    ]
    assert all(row.error is None and row.data for row in results)
    assert json.loads(context_refs)["knowledge_document_id"] == document_id
    assert run == (AgentRunState.COMPLETED.value, "SENT")
    assert llm.calls == [("converse", ("请综合查询",))] * 2
    assert [call.provider_call_id for call in llm.conversation_prompts[1].tool_calls] == [
        "provider-application-call",
        "provider-mail-call",
        "provider-knowledge-call",
    ]
    assert [call[0] for call in qq.calls] == ["deliver"]


def test_composite_agent_run_classifies_fake_llm_contract_failure(
    database: Database, settings: AppSettings, fake_clock: FakeClock
) -> None:
    _, _, chroma = _seed_composite(database, settings, fake_clock)
    qq = FakeQQGateway()
    process_run(
        _services(
            database,
            fake_clock,
            FakeLLM(error=LLMContractError("conversation", contract_reason="schema_failure")),
            qq,
            chroma,
        ),
        "composite-run",
    )
    with database.engine.connect() as connection:
        run = connection.execute(
            text("SELECT state, delivery_state, error, next_retry_at FROM agent_runs")
        ).one()
    assert run.state == AgentRunState.DELIVERY_PENDING.value
    assert run.delivery_state is None
    assert "contract_error" in run.error
    assert run.next_retry_at is not None
    assert qq.calls == []
