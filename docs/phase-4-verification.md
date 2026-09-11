# Phase 4 Verification

Phase 4 connects the QQ Conversation Agent to the existing Phase 1
`UpdateApplicationStatus` transaction without granting the LLM direct
business-write access.

## Verified Flow

1. A natural-language request produces one `UpdateApplicationStatus` tool call.
2. The runtime validates the proposal and persists a frozen `PendingAction`.
3. The original `AgentRun` becomes `WAITING_USER_CONFIRMATION`; no
   `Application`, `JobEvent`, or status-change `OutboxEvent` is written.
4. Confirmation and rejection messages remain durable inbound messages and
   become ordinary durable command runs.
5. The command run routes `确认`/`拒绝` before calling the LLM.
6. Confirmation executes the frozen `resolved_arguments` through the existing
   Phase 1 transaction, persists one `ToolResult`, resumes the original run,
   and sends its final reply through QQ.
7. Rejection persists a rejected result, resumes the original run, and leaves
   business facts unchanged.
8. Duplicate confirmation and action recovery remain idempotent.
9. A persisted write `ToolCall` without a result rebuilds its proposal from
   stored arguments without calling the LLM again.
10. Confirmation prompts are persisted before QQ delivery; failed delivery is
    recorded and retried by the lifecycle worker.
11. The lifecycle worker reconciles terminal actions whose original run was
    interrupted before leaving `WAITING_USER_CONFIRMATION`.

## Commands

```bash
uv run pytest tests/integration/test_conversation_agent.py -q
uv run pytest tests/e2e/test_agent_write_operations.py -q
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic check
uv run alembic current
```

The Phase 4 implementation uses the existing `0005_conversation_agent`
migration head. No migration is required because Phase 1 context references
already provide `PendingAction.session_id`, `agent_run_id`, and `tool_call_id`.

## Scope Guard

The registry contains the eight Phase 3 query tools plus exactly one write
tool: `UpdateApplicationStatus` with `permission=WRITE` and
`requires_confirmation=true`. `AddKnowledge`, `RemoveKnowledge`, and RAG
tools remain unregistered.

## Acceptance Result

Accepted on 2026-09-10 with Python 3.13.9 and SQLite 3.50.4.

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 54 tests |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 94 files formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS: no new upgrade operations |
| `uv run alembic current` | PASS: `0005_conversation_agent (head)` |

The HTTP E2E test also caught and locked two runtime issues that narrower tests
did not exercise: worker Run claiming now passes a sequence correctly to
SQLAlchemy `in_()`, and deterministic command replies flush their transcript
message before assigning the `AgentRun.final_message_id` foreign key.
Recovery tests additionally lock the crash windows between persisted ToolCall
and proposal creation, persisted confirmation prompt and QQ delivery, and
terminal action commit and original-run resumption.
