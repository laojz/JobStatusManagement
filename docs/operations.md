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

完整的中文服务器端人工验证与故障排查手册见
[`docs/deployed-manual-validation-troubleshooting.zh-CN.md`](deployed-manual-validation-troubleshooting.zh-CN.md)。

## HTTP probes and response matrix

Use `/live` to decide whether the process is answering and `/health` to decide
whether it should receive work. `/live` is deliberately dependency-free: it
does not query SQLite or any external capability and returns HTTP 200 with
`{"status":"alive"}` while the application can answer.

`/health` preserves its existing JSON fields and is readiness-oriented. It is
HTTP 200 only when SQLite is readable, the schema is exactly
`0008_qq_reply_targets`, and the configured capability state is ready or
explicitly local-only. Missing or non-head schema is HTTP 503 with
`database=not_ready`; a failed database read is HTTP 503 with
`database=unavailable`.

| Observed condition | `/live` | `/health` | Action |
| --- | ---: | ---: | --- |
| Process answers; database/schema ready; no failed or stale tasks | 200 | 200, `durable_tasks=ok` | Continue normal operation. |
| Database unavailable | 200 | 503, `database=unavailable` | Do not admit work; inspect path, permissions, locks, and logs. |
| Schema missing or not at head | 200 | 503, `database=not_ready` | Run migration and `current` against the configured database; do not use a side database. |
| Local IMAP and LLM disabled | 200 | 200, `product_readiness=local_only` | Treat as local-only, not as external-provider readiness. |
| Enabled capability unavailable or production fake adapter | 200 | 503, `product_readiness=not_ready` | Correct configuration or adapter wiring; do not claim provider compatibility. |
| Failed or stale durable task with otherwise-ready dependencies | 200 | 200, `durable_tasks=degraded` | Do not restart solely because of durable degradation; inspect and recover the task. |

The process supervisor is intentionally not specified by product name or an
unverified service artifact in this repository. If one is added, it should
restart only for a failed process-level liveness policy or an explicitly
approved readiness policy, never because a single durable task is degraded.
It must keep one process against one SQLite database and forward SIGTERM.

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

`durable_tasks=degraded` is an explicit persisted-work condition, not process
death. Run `failures --include-stale` and use the matching operation only after
identifying the task kind and state:

| Task state | Operator response |
| --- | --- |
| Failed outbox, notification, pending action, or knowledge task | Inspect the bounded error and ID, then use the corresponding `retry` command when the business impact is understood. |
| Stale notification, pending action, agent run, or knowledge indexing task | Confirm startup recovery ran, inspect again with `--include-stale`, and retry or escalate according to the task kind. |
| Failed or stale `AgentRun` | Inspect and recover through the normal lifecycle; there is intentionally no unsafe manual retry command. |
| Ambiguous external delivery | Record the ambiguity and stop; do not blindly retry or claim exactly-once delivery. |

The five-minute stale threshold applies to `SENDING`, `INDEXING`, `EXECUTING`,
and `RUNNING` durable states. `durable_tasks=ok` means the health query found no
failed or stale rows; it does not prove provider compatibility or zero current
in-flight work.

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

SQLite is the knowledge source of truth and Chroma is a rebuildable index. Only `ACTIVE` plus `READY` documents are searchable; `REMOVED` documents are excluded before cleanup. `KnowledgeChunk.id` is the Chroma record ID. Stop or quiesce the application before rebuilding so no worker concurrently changes the index. The command verifies the schema is at `0008_qq_reply_targets (head)` and reports typed counts; partial document failures produce a nonzero exit.

```bash
uv run python -m jobs_status_manager rebuild-chroma
```

## Restart

Startup scans stale Notification `SENDING`, Knowledge `INDEXING`, AgentRun `RUNNING`, and PendingAction `EXECUTING` before worker loops begin. Workers use AnyIO structured concurrency and database-driven scans. Shutdown cancels the task group and disposes the database after child tasks leave, so durable work remains retryable or becomes explicitly failed after a crash.

The current database head is `0008_qq_reply_targets`; health reports phase 6 and schema readiness without exposing business data. A real Bailian smoke completed with `text-embedding-v4` returning 1024 finite values. The key was loaded from the ignored `.env` file, and its value was not recorded. The follow-up rebuild and retrieval used isolated temporary paths, not production data, and reported `documents=1 ready=1 failed=0 chunks=1 upserted=1 partial_failure=False` with one matching document and a valid score. Temporary script and data were removed.
