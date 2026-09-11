# Operations

Run one process for one user, mailbox, and QQ account. SQLite WAL is the durable task coordinator; do not run two processes against one database. No Redis, Celery, RabbitMQ, or Kafka is required.

## Start and inspect

```bash
uv sync
cp .env.example .env
chmod 600 .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager health
uv run python -m jobs_status_manager run
curl --fail http://127.0.0.1:8000/health
```

The QQ endpoint is `POST /webhooks/qq` with `x-qq-webhook-token` when configured. Webhook intake is durable and idempotent; long LLM, tool, file, embedding, and Chroma work runs after the quick 2xx response.

## Failures and retry

```bash
uv run python -m jobs_status_manager failures
uv run python -m jobs_status_manager failures --include-stale
uv run python -m jobs_status_manager retry outbox <OUTBOX_EVENT_ID>
uv run python -m jobs_status_manager retry notification <NOTIFICATION_ID>
uv run python -m jobs_status_manager retry pending_action <PENDING_ACTION_ID>
uv run python -m jobs_status_manager retry knowledge_index <KNOWLEDGE_DOCUMENT_ID>
uv run python -m jobs_status_manager retry knowledge_cleanup <KNOWLEDGE_DOCUMENT_ID>
```

Failure output contains bounded errors and IDs, not message bodies or credentials. Retry only requeues an existing failed record and never bypasses PendingAction confirmation. AgentRun records are inspectable but deliberately have no unsafe manual retry operation.

Outbox and Notification delivery are at-least-once with persisted idempotency. An uncertain QQ timeout may produce a duplicate external message, but not a duplicate Application, JobEvent, or KnowledgeDocument fact.

## Backup, restore-check, integrity

```bash
mkdir -p ./backups
uv run python -m jobs_status_manager backup ./backups/jobs-$(date +%Y%m%d-%H%M%S).db
uv run python -m jobs_status_manager integrity-check
uv run python -m jobs_status_manager restore-check ./backups/jobs-YYYYMMDD-HHMMSS.db
```

Backup destinations must not be the live database or an existing file. The command uses SQLite's backup API and validates the result. `restore-check` validates in an isolated temporary database and never replaces the live file. Keep database, WAL sidecars, Chroma, uploads, and backups owner-private.

## Knowledge index

SQLite is the knowledge source of truth and Chroma is a rebuildable index. Only `ACTIVE` plus `READY` documents are searchable; `REMOVED` documents are excluded before cleanup. `KnowledgeChunk.id` is the Chroma record ID. Stop or quiesce the application before rebuilding so no worker concurrently changes the index. The command verifies the schema is at `0007_phase6_reliability (head)` and reports typed counts; partial document failures produce a nonzero exit.

```bash
uv run python -m jobs_status_manager rebuild-chroma
```

## Restart

Startup scans stale Notification `SENDING`, Knowledge `INDEXING`, AgentRun `RUNNING`, and PendingAction `EXECUTING` before worker loops begin. Workers use AnyIO structured concurrency and database-driven scans. Shutdown cancels the task group and disposes the database after child tasks leave, so durable work remains retryable or becomes explicitly failed after a crash.

The current database head is `0007_phase6_reliability`; health reports phase 6 and schema readiness without exposing business data. A real Bailian smoke completed with `text-embedding-v4` returning 1024 finite values. The key was loaded from the ignored `.env` file, and its value was not recorded. The follow-up rebuild and retrieval used isolated temporary paths, not production data, and reported `documents=1 ready=1 failed=0 chunks=1 upserted=1 partial_failure=False` with one matching document and a valid score. Temporary script and data were removed.
