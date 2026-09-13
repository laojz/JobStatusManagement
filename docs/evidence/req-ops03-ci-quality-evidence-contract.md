# REQ-OPS03 CI Quality and Evidence Contract

**Status:** `Operational Gap`. Local quality output is not a verified CI run.

## Required gates

On every candidate commit, a clean, controlled environment must run the
relevant tests and quality checks, including `pytest`, Ruff, format,
basedpyright, `uv lock --check`, `uv run alembic check`, and `git diff --check`.
The pipeline must use an explicit environment rather than an incidental local
`.env`. A failed gate blocks release artifact generation.

The evidence record must state the exact test selection and resulting counts.
It must also record schema head and the database target used by migration and
application checks. QQ credentials and live QQ traffic are not CI gates.

## Required evidence fields

Record date/time, commit or artifact, environment, command, exit code, schema
head, counts, cleanup, and limitations for each gate. Store the redacted result
with the candidate commit and make failures directly traceable.

## Open Decisions

- **CI provider and artifact storage:** `OPEN`, select and approve both.

## Rehearsal boundary

All quality commands and credential-free tests can be rehearsed locally. CI
closure requires a selected provider, controlled runner, retained artifact
storage, and an actual run bound to a candidate commit. No CI run, artifact,
checksum, or test result is fabricated here. QQ remains excluded, with no live
QQ provider steps.
