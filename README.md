# Jobs Status Manager

This repository contains the Phase 0 foundation through the Phase 6 reliability
and operations workflow: a single-process Python application with safe configuration, SQLite
WAL migrations, identity bootstrap, deterministic PendingAction confirmation,
atomic business writes, QQ file intake, and a rebuildable Chroma index.

## Quick start

```bash
uv sync
cp .env.example .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager run
```

Then query `http://127.0.0.1:8000/health`. Stop the process with `Ctrl-C`.

Phase 1 adds only Application, JobEvent, PendingAction, and OutboxEvent. Run
the local vertical-loop demo with:

```bash
uv run python -m jobs_status_manager demo-status-core
```

Run the credential-free Phase 2 fake pipeline with:

```bash
uv run python -m jobs_status_manager demo-mail-pipeline
```

Phase 3 adds a token-protected QQ webhook, durable idempotent conversation
intake, sequential Application/Mail tools, bounded AgentRun execution, and
fake QQ delivery. Phase 4 adds the confirmation-gated
`UpdateApplicationStatus` write tool. Phase 5 adds `.txt`, `.md`, `.markdown`,
`.pdf`, and `.docx` upload parsing, confirmation-gated `AddKnowledge` and
`RemoveKnowledge`, deterministic chunks, injected Embedding/Chroma workers,
and `SearchKnowledge`. SQLite remains the knowledge source of truth while
Chroma is a rebuildable local index. Configure `APP_EMBEDDING_API_KEY` and
`APP_CHROMA_PATH` together to enable Alibaba Cloud Bailian indexing and the
stopped-process `rebuild-chroma` command.

## Acceptance status

Phase 0 acceptance passed on 2026-09-09 before Phase 1 work began. Its
historical result was 10 passing tests, clean Ruff and basedpyright checks,
and Alembic revision `0001_identity (head)`. Phase 1 and Phase 2 were also
accepted. Phase 3 acceptance passed on 2026-09-10. Phase 4 acceptance passed on
the same day with 54 passing tests. Phase 5 core implementation verification
passed with 58 tests, clean Ruff, format, basedpyright, and source-policy
checks, plus a successful `0006 -> 0005 -> 0006` Alembic round trip. Full Phase
5 acceptance remains partial: the credential-free real `LocalChroma` temporary-path
rebuild/search drill and the real Bailian provider smoke passed. The complete QQ file-to-RAG E2E
and the three-tool composite Agent scenario remain pending.
Phase 6 acceptance passed on 2026-09-11 with 120 tests, clean Ruff, format,
basedpyright, and Alembic checks, a real Bailian `text-embedding-v4` 1024-dimensional
finite-vector smoke, isolated CLI rebuild counts, and successful matching retrieval.
The current database revision is `0007_phase6_reliability (head)`.
The credential-free mail demo also passed with one outbound fake QQ push and
no automatic application change. See
[`docs/phase-0-verification.md`](docs/phase-0-verification.md) for the Phase 0
record, [`docs/phase-1-verification.md`](docs/phase-1-verification.md) for the
Phase 1 implementation and current verification, and
[`docs/phase-2-verification.md`](docs/phase-2-verification.md) for the Phase 2
acceptance record and scope boundary, and
[`docs/phase-3-verification.md`](docs/phase-3-verification.md) for Phase 3.
Phase 4 details are recorded in
[`docs/phase-4-verification.md`](docs/phase-4-verification.md), and Phase 5 in
[`docs/phase-5-verification.md`](docs/phase-5-verification.md).
Phase 6 operations and deployment are documented in
[`docs/operations.md`](docs/operations.md), [`docs/deployment.md`](docs/deployment.md),
and [`docs/phase-6-verification.md`](docs/phase-6-verification.md).

Run the complete verification suite with:

```bash
uv run pytest -q
```
