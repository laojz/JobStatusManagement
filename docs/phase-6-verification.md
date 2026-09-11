# Phase 6 Verification

Credential-free evidence from the repository verification remains preserved below. A real Bailian provider smoke and isolated temporary-path CLI verification were also completed on 2026-09-11. The API key was loaded from the ignored `.env` file; its value and all sensitive provider data were excluded from this record. No production indexing path or production data was used.

| Command | Result |
| --- | --- |
| `uv run pytest -q` | PASS: 120 tests passed (118 before the real LocalChroma and CLI cleanup regression tests were added) |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 136 files already formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| changed Python file LSP diagnostics | PASS: no diagnostics |
| `uv run alembic check` | PASS: No new upgrade operations detected |
| `uv run alembic current` | PASS: `0007_phase6_reliability (head)` |
| `git diff --check` | PASS |
| `uv run python -m jobs_status_manager --help` | PASS: all 12 command names preserved |
| credential-free temp-path `migrate` / `bootstrap` / `health` | PASS: migrated, created identity, `ready phase=6 schema_version=0007_phase6_reliability` |
| targeted lifecycle/CLI/RAG tests | PASS: focused adapter, lifecycle, settings, knowledge, health, and CLI tests; full suite 120 tests |
| real LocalChroma temporary-path integration | PASS: `rebuild_index` and search with deterministic `FakeEmbedding` and isolated SQLite |
| real Bailian embedding smoke | PASS: provider `bailian`, model `text-embedding-v4`, dimensions `1024`, finite values `true` |
| isolated CLI rebuild and retrieval | PASS: `documents=1 ready=1 failed=0 chunks=1 upserted=1 partial_failure=False`; retrieval returned one matching document with a valid score |
| temporary verification artifacts | PASS: temporary script and data were removed; no production data or exact temporary path was recorded |
| CLI pure LOC check | PASS: `cli.py` 13, support 16, operations 177, runtime 20, demos 123 |

Covered: bounded/redacted external errors across durable workers and logs, Outbox retry/idempotency, Notification retry and stale `SENDING`, direct NotificationAttempt field persistence, direct ProcessedEvent uniqueness enforcement, PendingAction stale `EXECUTING` without duplicate JobEvent, AgentRun stale `RUNNING` to explicit `FAILED`, Knowledge stale `INDEXING` attempt-ceiling recovery, reset-failure ineligibility, partial fake Chroma rebuild, cleanup retry operator messaging, backup/restore isolation, migration 0007 AgentRun defaults and all declared indexes, Bailian wire contract and secret-safe errors, production adapter ownership, rebuild CLI config/head/count/partial-failure paths, and AnyIO lifecycle shutdown. Boundary coverage also includes blank or whitespace embedding key normalization to `None`, a 64-character ASCII provider error classification grammar, suppression of sensitive transport and malformed-response traceback causes, paired factory ownership, preservation of Chroma construction errors when cleanup fails, and real LocalChroma rebuild/search on temporary SQLite with deterministic `FakeEmbedding`. The real Bailian smoke and isolated CLI retrieval verification used aggregate results only and did not record input text, document content, vector values, raw provider responses, authorization data, production paths, or sensitive values.

The repository-wide no-excuse audit also reports pre-existing violations in
untouched legacy modules/tests (oversized modules, mutable test fakes, and
existing broad typing); no production module changed in this verification exceeds 250 pure LOC.
