# Local development

## Requirements

- Python `>=3.12,<3.14`
- `uv`

```bash
uv sync
cp .env.example .env
chmod 600 .env
```

The example is credential-free. The application creates data paths with owner-only permissions where supported.

## Run

```bash
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager run
```

The process serves `GET /health`; Ctrl-C, SIGINT, and SIGTERM use structured shutdown. Repeat `migrate` and `bootstrap` safely. The schema head is `0007_phase6_reliability`.

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
