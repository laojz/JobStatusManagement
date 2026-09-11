# Phase 3 Verification

Phase 3 is the QQ Conversation Agent vertical slice. It accepts token-protected
QQ text events, persists an idempotent conversation and durable AgentRun, and
processes one sequential read-only Application/Mail tool call at a time.

## Scope Verified

- Migration `0005_conversation_agent` creates sessions, transcript messages,
  AgentRuns, ToolCalls, ToolResults, and QQ event/message identity records.
- The webhook validates the token and configured sender, persists before
  returning `202`, and does not call the LLM, tools, or QQ outbound gateway.
- Same-session work is serialized with `RUNNING` and `QUEUED` states.
- `ConversationPrompt` carries the system instruction, summary, active entity
  IDs, bounded recent messages, current user message, and structured prior
  tool results; persisted context references update the Session transactionally.
- Restart recovery resumes a persisted pending ToolCall or delivery-pending
  answer rather than creating a new sequence or assistant message.
- The registry exposes exactly eight read-only tools and rejects write/RAG or
  unknown names.
- Tool arguments are parsed by per-tool frozen Pydantic schemas with forbidden
  extra fields, and registry metadata exposes input/output shape information.
- Runtime work is bounded by eight tool calls, a 120-second run deadline, a
  per-tool timeout, and a 12,000-character result limit.
- LLM, tool, and QQ calls occur outside database transactions; failures are
  persisted on AgentRun/ToolResult, including QQ delivery state and provider ID.
- `qq_user_openid` is required configuration for the single-user sender map.

## Verification Commands

The following commands were run from the repository root on 2026-09-10:

```text
uv run pytest
45 passed in 1.53s
uv run ruff check .
All checks passed!
uv run ruff format --check .
86 files already formatted
uv run basedpyright
0 errors, 0 warnings, 0 notes
uv run alembic check
No new upgrade operations detected.
uv run alembic current
0005_conversation_agent (head)
```

## Credential-Free HTTP Scenario

The Starlette TestClient scenario in
`tests/integration/test_conversation_agent.py` verified invalid-token rejection,
accepted `202` persistence, duplicate event/message idempotency, one active run,
one queued same-session run, stale-run recovery, the eight-call limit, and
forbidden write-tool rejection. The health E2E scenario verified the live
`/health` surface reports phase `3` and schema `0005_conversation_agent`.

No real LLM, QQ, IMAP, embedding, or Chroma credentials are required.

The explicit manual TestClient run returned:

```text
{'status_code': 202, 'body': {'status': 'accepted'}, 'outbound_calls': ['validate_webhook', 'receive']}
```

The absence of `push` in that immediate call list confirms the webhook returns
after persistence and validation without invoking outbound QQ delivery.

## Scope Boundary

Phase 4 write tools and Phase 5 RAG tools are not registered or executable.

## Acceptance Decision

Phase 3 was accepted on 2026-09-10. All Phase 3 Definition of Done items are
complete, the full repository verification suite is green, and the database is
at `0005_conversation_agent (head)`. Phase 4 Agent write operations and Phase 5
RAG remain explicitly outside the accepted scope.
