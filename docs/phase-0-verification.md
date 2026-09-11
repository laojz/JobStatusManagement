# Phase 0 verification

This sequence is the acceptance path for the foundation only.

## Phase 0 acceptance result

Phase 0 was accepted at the Phase 0 boundary on 2026-09-09 using Python
3.13.9, SQLite 3.50.4, and the locked `uv` environment. The revision and test
counts below are historical Phase 0 acceptance values and intentionally do not
include Phase 1 changes.

Automated gates:

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 10 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 48 files already formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS: no new upgrade operations |
| `uv run alembic current` | PASS: `0001_identity (head)` |

Fresh-database smoke verification also passed:

- Migration created `schema_migrations`, `users`, and `mail_accounts` only.
- The first bootstrap created one user and one mail account.
- The second bootstrap reused both records with `created=False`.
- The resulting counts remained `users=1` and `mail_accounts=1`.
- SQLite reported `journal_mode=wal` and `busy_timeout=5000`.
- The health command reported `ready phase=0 schema_version=0001_identity`.
- The HTTP health endpoint returned HTTP 200 after startup.
- The process shut down cleanly and could be restarted against the same database.

No Phase 1+ business tables, business write routes, external service clients,
workers, queues, Redis, or container configuration were added.

## Current repository status

Phase 1 Application Core has since been implemented and accepted. The current
database head is `0003_pending_action_context_refs`; this does not change the
Phase 0 acceptance result above. For the current implementation status, see
the Phase 1 section in `docs/implementation-plan.md` and the project README.

## Automated gates

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

## Fresh local smoke

```bash
rm -rf ./data
cp .env.example .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager health
uv run python -m jobs_status_manager run
```

In another terminal:

```bash
curl -i http://127.0.0.1:8000/health
```

The response must be HTTP 200 and contain only `status`, `phase`, `database`, and `schema_version`. Stop the process with `Ctrl-C`, start `run` again, and repeat the health request. The database must still be readable and the second bootstrap must not add a second user or mail account.

## Phase 0 scope checks

The only migration-created domain tables are `users` and `mail_accounts`, plus Alembic's `schema_migrations`. Phase 0 intentionally has no Application, JobEvent, Mail, Session, AgentRun, Notification, KnowledgeDocument, Outbox, ProcessedEvent, route, service, worker, or external client implementation.
