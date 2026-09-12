# Local development

## Requirements

- Python `>=3.12,<3.14`
- `uv`

```bash
uv sync
cp .env.example .env
chmod 600 .env
```

The example is credential-free. The application creates data paths with owner-only permissions where supported. `APP_QQ_ENABLED` defaults to `false`, so even complete QQ credentials do not construct or start production botpy unless the operator explicitly enables the gate. Setting only one credential is a configuration error; when the gate is enabled, `APP_QQ_TOKEN_BASE_URL` must also be explicit and both QQ base URLs must use HTTPS. `https://bots.qq.com` is an unverified legacy fallback, not a default. The listener uses `APP_HOST`/`APP_PORT`; `APP_QQ_WEBHOOK_PATH` controls the route.

## Run

```bash
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager run
```

The process serves `GET /health` and owns `POST /webhooks/qq`; Ctrl-C, SIGINT, and SIGTERM use structured shutdown. Run one process only: do not start botpy's own HTTP server or a second listener. Repeat `migrate` and `bootstrap` safely. The schema head is `0008_qq_reply_targets`.

## Demos and quality

```bash
uv run python -m jobs_status_manager demo-status-core
uv run python -m jobs_status_manager demo-mail-pipeline
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic current
uv run alembic check
```

Phase 6 operations are documented in `docs/operations.md` and `docs/deployment.md`. Real Bailian smoke was verified separately with `text-embedding-v4` returning 1024 finite values. The API key was loaded from the ignored `.env` file without recording its value. The isolated CLI rebuild and retrieval used temporary paths only, returned one matching document with a valid score, and left no temporary script or data behind.

For a safe QQ-free smoke, run the migration, bootstrap, fake mail demo, and
`health` command above. Live QQ URL validation, C2C receive/reply, proactive
push, attachment receive, and ordinary-file send require an operator-approved
credential-gated smoke; see `docs/deployment.md`.
