# Current Evidence Baseline

**Date:** 2026-09-13

The first record below preserves the observed pre-fix baseline. The second
record is the current controlled verification after the local CFG01, CFG02,
F01, F02, F03, and CFG03 changes. This is still a worktree result, not a
release-artifact result.

| Field | Record |
|---|---|
| Commit/artifact | Current worktree; no commit hash or artifact identifier recorded |
| Environment | Local repository environment; default working-directory `.env` was present and affected readiness assertions |
| Command | `uv run pytest -q` |
| Exit code | Non-zero, exact numeric code not preserved in the supplied baseline |
| Result | **Before fixes:** `373 passed + 2 failed` |
| Schema head | `0008_qq_reply_targets (head)` |
| Cleanup | No cleanup result recorded for this test run |
| Limitations | The two failures were readiness fixture failures affected by working-directory IMAP and LLM enablement. This is not a green release result and does not establish `375 passed`. |

| Field | Record |
|---|---|
| Commit/artifact | Current worktree based on `62c5ad427a0796c88709cc15dbaae52e6d1e0216`; changes are uncommitted |
| Environment | Controlled pytest fixture; ambient `APP_*` values removed, IMAP and LLM explicitly disabled |
| Command | `uv run pytest -q` |
| Exit code | `0` |
| Result | **Current verification:** `404 passed` |
| Schema head | `0008_qq_reply_targets (head)` |
| Cleanup | Test temporary databases and resources disposed by fixtures; no repository database created |
| Limitations | Worktree evidence only; no CI artifact, release checksum, live QQ, real-provider business E2E, or online recovery evidence |

| Field | Record |
|---|---|
| Commit/artifact | Current worktree based on `62c5ad427a0796c88709cc15dbaae52e6d1e0216`; changes are uncommitted |
| Environment | Isolated temporary directory with explicit `APP_DATABASE_PATH` |
| Command | `uv run alembic upgrade head && uv run alembic check && uv run alembic current` |
| Exit code | `0` |
| Result | `PASS`; no new upgrade operation; current `0008_qq_reply_targets (head)` |
| Schema head | `0008_qq_reply_targets (head)` |
| Cleanup | Temporary directory and database removed after verification |
| Limitations | Same-target CLI health was separately verified with the same explicit temporary database; this remains worktree evidence until CI/artifact binding exists |

## Remaining follow-up evidence

- Bind the controlled suite and same-target migration/health result to a
  release artifact or CI run.
- Do not report `375 passed` as the current result; the current expanded suite
  observed in this worktree is `404 passed`.
- Record the local deterministic QQ contracts separately from any future live
  QQ process or provider validation. No live QQ result is recorded here.
- Local fake IMAP business-path and composite AgentRun E2E evidence is recorded.
  Only approved real-provider business evidence remains open for those paths.

## Related records

- [Requirements matrix](requirements-matrix.md)
- [Authoritative requirements](../non-qq-production-readiness-requirements-agent-input.zh-CN.md)
- [Adapter verification snapshot](../imap-llm-adapter-verification.md), historical or limited paths only
