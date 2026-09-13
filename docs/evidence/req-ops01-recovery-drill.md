# REQ-OPS01 Recovery Drill Checklist

**Status:** `Operational Gap`. A local fixture-level recovery drill ran and is
recorded below. Full operational closure, including approved numeric RPO/RTO
targets and measurement against them, remains open.

## Scope and order

Use an isolated directory and a stopped or quiesced single-process runtime.
Inventory the SQLite database and WAL sidecars, application data, uploads, and
the source inputs required to rebuild Chroma. Back up the database with
`backup`, validate it with `restore-check`, restore only into the isolated
target, then run `integrity-check`, `migrate` only when the approved procedure
requires it, `health`, and `rebuild-chroma`. Compare schema head, representative
business counts, upload counts, and index counts. Never replace the live file.

## Checklist

- [x] Record temporary source and isolated target directories, selected worktree, and environment.
- [x] Capture a consistent SQLite backup after WAL-capable writes and retain an application upload input.
- [x] Run `restore-check` before using the copy; run `check_integrity` after restore.
- [x] Use the migration and bootstrap APIs required by the fixture; record that health was not run.
- [x] Rebuild the restored local Chroma path and record documents, ready, failed, chunks, upserted, and partial-failure results.
- [ ] Record observed loss window and elapsed restore time against approved RPO and RTO targets.
- [x] Remove the temporary backup, restored database, upload copy, Chroma path, and outputs, and assert cleanup.

## Required evidence fields

Record date/time, commit or artifact, environment, command, exit code, schema
head, counts, cleanup result, and limitations. Also record RPO/RTO targets and
observed values, but never invent numeric targets.

## Open Decisions

- **Numeric RPO:** `OPEN`, owner and approval required.
- **Numeric RTO:** `OPEN`, owner and approval required.

## Rehearsal boundary

The database backup, isolated `restore-check`, integrity check, local fixtures,
and Chroma rebuild can be rehearsed locally. Closure still requires an approved
scope covering all durable inputs and numeric RPO/RTO values. No live recovery
result is asserted. QQ is excluded, and no live QQ credential, endpoint,
message, attachment, or provider step belongs in this record.

## Local Execution Record

- **Date/time:** `2026-09-13T08:20:30Z` command environment; test execution was local.
- **Selected worktree:** `62c5ad4` short identity; worktree was dirty and no commit was created.
- **Environment:** macOS; Python `3.13.9`; SQLite `3.50.4`; repository virtual environment via `uv`.
- **Source directory:** pytest-managed temporary `tmp_path/data`, containing the live test database and upload input.
- **Isolated target:** pytest-managed `tmp_path/recovery-drill/{backup,restored}`, never the live database path.
- **Command:** `uv run pytest -q tests/integration/test_recovery_drill.py`
- **Exit code:** `0`; result `1 passed` in `0.84s` on the recorded run.
- **Backup and validation:** `backup_database` created a separate `jobs.db`; `restore_check` passed; post-copy `check_integrity` passed.
- **SQLite/WAL:** the source `Database` reported `journal_mode=wal`; the test committed an application update before native SQLite backup.
- **Schema head:** source and isolated restore validated as `0008_qq_reply_targets`.
- **Representative counts before and after:** `applications=1`, `user_files=1`, `knowledge_documents=1`, `knowledge_chunks=1`.
- **Rebuild result:** `total_documents=1`, `ready_documents=1`, `failed_documents=0`, `total_chunks=1`, `upserted_chunks=1`, `partial_failure=false`; restored LocalChroma search returned the restored document.
- **Lifecycle steps:** migration and bootstrap APIs were used by the fixture; no separate `health` command was run; no live provider was contacted.
- **Cleanup:** the test removed the isolated drill directory and source upload fixture, then asserted both paths were absent.
- **Limitations:** this is a local fixture-level drill, not a production backup, live recovery, release-readiness result, or numeric RPO/RTO measurement. The test does not approve RPO/RTO values, exercise QQ, or establish full operational closure. The repository has no dedicated restore API; isolated restoration is represented by copying the validated backup into a fresh temporary target.
