# Phase 1 verification

This sequence is the acceptance path for the Application Core vertical loop.
It intentionally does not include Phase 2+ Mail, QQ, Agent, Notification, or
RAG functionality.

## Phase 1 acceptance result

Phase 1 Application Core was accepted on 2026-09-09 using Python 3.13.9,
SQLite 3.50.4, and the locked `uv` environment.

Automated gates:

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 23 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 63 files already formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS: no new upgrade operations |
| `uv run alembic current` | PASS: `0003_pending_action_context_refs (head)` |

The local status-core demo also passed:

```text
proposal state=PENDING code=PA-DESK
confirmation=CONFIRMED execution=COMPLETED
changed=True
```

## Acceptance coverage

The automated suite verifies:

- Application status values and interview-round validation.
- NFKC, whitespace, case, punctuation, and empty-department normalization.
- Exact normalized Application matching and uniqueness.
- First Application creation with `previous_status = null`.
- Status changes, interview-round-only changes, and no-op updates.
- PendingAction proposal, seven-day expiry, frozen arguments, fingerprint, and
  complete lifecycle states.
- Deterministic `确认` / `拒绝` routing, confirmation codes, ownership, and
  multiple-action ambiguity protection.
- Atomic Application, JobEvent, OutboxEvent, and PendingAction completion.
- Transaction rollback after an injected failure, with no partial business
  records.
- Repeated confirmation and repeated execution idempotency.
- Recovery of persisted confirmed actions.
- Preservation of Phase 0 identity tables and SQLite foreign-key enforcement.
- The absence of Phase 2+ Mail, QQ, Agent, Notification, RAG, and external
  service implementation.

## Migration and database state

Phase 1 adds the following migrations:

```text
0002_application_core
0003_pending_action_context_refs
```

The current schema head is:

```text
0003_pending_action_context_refs (head)
```

The latter migration adds nullable future-runtime context references to
PendingAction (`session_id`, `agent_run_id`, `tool_call_id`, and
`mail_analysis_id`) without creating Phase 2+ tables.

## Local acceptance commands

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

With a credential-free configuration and a writable data directory:

```bash
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager demo-status-core
```

The demo must show a `PENDING` proposal, a confirmed action, a completed
execution, and a changed Application. Before confirmation, no Application or
JobEvent is modified; repeated execution does not create another JobEvent.

## Scope boundary

Phase 1 contains only the Application Core vertical loop:

```text
Proposal → PendingAction → Confirmation → Application/JobEvent/Outbox
```

No LLM, QQ Webhook, IMAP, Mail analysis, Notification Dispatcher, RAG,
Chroma, ProcessedEvent, queue, Redis, or distributed infrastructure is part
of this acceptance.
