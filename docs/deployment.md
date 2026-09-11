# Deployment

Run one process for one user, mailbox, and QQ account. SQLite WAL, local Chroma, uploads, and the database are local durable state. Do not run a second process against the same database.

```bash
uv sync
cp .env.example .env
chmod 600 .env
chmod 700 ./data
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager health
uv run python -m jobs_status_manager run
```

Use a supervisor that runs one private process, forwards SIGTERM, and restarts on failure. Keep `.env`, database/WAL files, Chroma, uploads, and backups private. Do not commit mailbox passwords, QQ credentials, LLM keys, or backup files.

```bash
curl --fail http://127.0.0.1:8000/health
uv run python -m jobs_status_manager failures --include-stale
mkdir -p ./backups
uv run python -m jobs_status_manager backup ./backups/jobs-$(date +%Y%m%d-%H%M%S).db
uv run python -m jobs_status_manager restore-check ./backups/jobs-YYYYMMDD-HHMMSS.db
uv run python -m jobs_status_manager integrity-check
```

Startup scans stale Notification `SENDING`, PendingAction `EXECUTING`, AgentRun `RUNNING`, and Knowledge `INDEXING` before worker loops. AnyIO structured shutdown leaves durable windows retryable or explicitly failed. Delivery is at-least-once; exactly-once is not claimed. Configure `APP_EMBEDDING_API_KEY` and `APP_CHROMA_PATH` together for Bailian embeddings and local Chroma. Stop or quiesce the runtime before `rebuild-chroma`.
