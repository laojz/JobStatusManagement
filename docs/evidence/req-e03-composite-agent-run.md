# REQ-E03 Composite AgentRun Evidence

**Date:** 2026-09-13

**Status:** `Evidence Required`

This record adds deterministic non-QQ evidence for the composite Application,
Mail, and SearchKnowledge AgentRun path. It does not close REQ-E03 and does not
establish provider compatibility or production readiness.

## Evidence Classes

| Evidence class | Observed scope | Status |
|---|---|---|
| Fake LLM | Direct `process_run()` execution with temporary SQLite, real Application/Mail/SearchKnowledge tool routing, fake embedding, fake Chroma, fake QQ delivery, persisted tool ordering and IDs, terminal state, and pending-tool restart replay | `RECORDED` |
| `MockTransport` | OpenAI-compatible adapter HTTP contract tests | `RECORDED` separately in the adapter verification record; not part of this E2E |
| Synthetic real LLM | Real adapter/provider request with synthetic input | `RECORDED` as a limited smoke; not a business-path E2E |
| Real provider business E2E | Approved real provider executing this composite AgentRun scenario | `NOT_RUN` |

## Executed Deterministic Evidence

| Field | Record |
|---|---|
| Commit/artifact | Current worktree; no commit or artifact identifier recorded |
| Environment | Local isolated pytest fixtures; temporary file-backed SQLite; no QQ webhook; no external provider; fake LLM, embedding, Chroma, and delivery adapters |
| Command | `uv run pytest -q tests/integration/test_composite_agent_evidence.py` |
| Exit code | `0` |
| Result | `2 passed` |
| Schema head | Existing migrated test database; no migration was added |
| Cleanup | Pytest temporary database disposed by fixtures; restarted database disposed by the test |
| Limitations | Proves the local AgentRun composition and persistence contract only. It does not prove OpenAI-compatible provider behavior, real LLM tool-call generation, QQ delivery, or online restart recovery. |

## Covered Assertions

- Application, Mail, and SearchKnowledge calls are persisted in sequence `1`,
  `2`, and `3`.
- Every persisted `ToolResult.tool_call_id` matches its corresponding
  `ToolCall.id`; all three successful results contain data.
- SearchKnowledge result references persist the knowledge document identity.
- A database disposal and reopen replays the incomplete first tool call,
  continues the remaining scripted calls, and produces one result per call.
- The successful run reaches `COMPLETED` with delivery state `SENT`.
- A fake `LLMContractError` is retained as `contract_error`; the existing
  runtime classifies it as retryable `DELIVERY_PENDING` with a retry timestamp,
  without delivery. This is classification evidence, not a claim that every
  provider contract failure should be terminal.

## Required Unexecuted Evidence

The following remains `Evidence Required` and must not be substituted by this
fake E2E, the adapter `MockTransport` tests, or synthetic real LLM smoke:

- An approved real-provider business E2E for the same composite AgentRun path.
- Real-provider tool-call generation, tool-result continuation, and provider
  error behavior under the supported deployment configuration.
- Any QQ live webhook, delivery, or online restart evidence, which remains
  outside this non-QQ record and human-gated.
