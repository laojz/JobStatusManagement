# Phase 2 verification

Phase 2 was accepted on 2026-09-10. The vertical closure is available at
Alembic revision `0004_mail_pipeline (head)`:

```text
IMAP -> Mail + MAIL_RECEIVED -> rule classifier
-> typed JobMailAnalysis + JOB_MAIL_ANALYZED
-> ordinary Notification or PendingAction confirmation Notification
-> NotificationAttempt -> QQ push
```

Status-update mail never writes `Application` or `JobEvent` before the existing
Phase 1 confirmation and execution services are called explicitly. Non-job mail
is retained and stops before LLM analysis. External providers are not
constructed by the lifecycle or the demo.

## Acceptance result

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 39 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 76 files already formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS: no new upgrade operations detected |
| `uv run alembic current` | PASS: `0004_mail_pipeline (head)` |
| `uv run python -m jobs_status_manager demo-mail-pipeline` | PASS: `pushes=1 application_changes=0` |

The accepted tests verify the following behavior:

- IMAP polling passes and persists the provider cursor, advances it only after
  durable ingestion, and preserves it when polling fails.
- Duplicate provider messages create one `Mail` and one `MAIL_RECEIVED` event;
  duplicate polling reports zero newly ingested messages.
- Non-job mail is retained without calling the LLM or creating analysis,
  PendingAction, JobEvent, or job-mail Notification records.
- Job-mail analysis is schema-validated, stored as one effective analysis per
  Mail, and placed into retry state when LLM analysis fails.
- Status suggestions create one frozen PendingAction through the Phase 1
  proposal path and do not modify Application or JobEvent before confirmation.
- Ordinary mail creates an ordinary Notification; status mail creates only a
  combined confirmation Notification with a confirmation code and the
  unconfirmed-state warning.
- Outbox consumers are idempotent through `ProcessedEvent`; missing referenced
  aggregates remain observable and retryable instead of being silently marked
  successful.
- Notification attempts are persisted before QQ push, provider failures are
  retried up to the configured limit, and stale `SENDING` rows recover to
  `RETRY_WAIT`.

## Scope boundary

This acceptance covers protocol and fake-adapter execution. It does not claim
that production IMAP, LLM, or QQ provider adapters are configured. QQ support
in Phase 2 is outbound `push()` only. QQ Webhook receive/reply, Session,
Conversation Agent, Tool Calling, natural-language write operations, file
parsing, Embedding, Chroma, and RAG remain out of scope.

## Verification commands

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run python -m jobs_status_manager demo-mail-pipeline
```

Tests cover migration constraints, typed envelope ingestion and duplicates,
classification, structured validation and retry, PendingAction confirmation
boundaries, Outbox/ProcessedEvent idempotency, notification success/failure,
stale recovery, cursor recovery, missing-aggregate retry behavior, and the
credential-free demo. QQ receive/reply/webhook,
sessions, AgentRun, tool calls/results, file parsing, knowledge, embeddings,
Chroma, and RAG remain out of scope.
