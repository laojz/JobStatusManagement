# REQ-CFG03 Health and Liveness Evidence

## Scope

This is local deterministic contract evidence for the Starlette HTTP surface.
It uses temporary SQLite databases, the existing lifecycle fixtures, and fake
or constructed local adapters. It does not validate QQ, IMAP, LLM, Chroma,
credentials, endpoints, or production supervisor behavior.

## Contract exercised

| Case | Expected result |
| --- | --- |
| `/live` with no migrated database or external capability | HTTP 200, `{"status":"alive"}`; `/health` remains HTTP 503. |
| Head schema, local IMAP/LLM disabled | `/health` HTTP 200 with the existing payload fields, `product_readiness=local_only`, and `durable_tasks=ok`. |
| Schema at `0007_phase6_reliability` | `/health` HTTP 503, `database=not_ready`, observed `schema_version=0007_phase6_reliability`. |
| Database revision lookup unavailable | `/live` HTTP 200; `/health` HTTP 503, `database=unavailable`, and `schema_version=null`. |
| One failed and one stale notification | `/health` HTTP 200 with `durable_tasks=degraded`, `failed_tasks=1`, and `stale_tasks=1`. This does not signal process death. |
| Production fake adapters | `/health` HTTP 503 with `product_readiness=not_ready`; capability fields remain `fake`. |
| Production constructed capability adapters | `/health` HTTP 200 with `product_readiness=ready`. |
| Local capability construction failure | `/health` HTTP 503 with capability fields and `product_readiness` marked `not_ready`. |

## Reproduction

From the repository root, with no live provider calls or credentials:

```text
uv run pytest -q tests/e2e/test_health.py tests/unit/test_lifecycle.py tests/unit/test_operations.py
```

Working-tree result on 2026-09-13: exit code 0, `399 passed`. Re-run this
command for any release artifact and record the date, artifact or commit,
environment, exit code, schema head, and cleanup.

## Operational interpretation

Configure `/live` as the process-level probe and `/health` as the readiness or
traffic probe. Do not restart solely because `/health` reports
`durable_tasks=degraded` with HTTP 200. Inspect `failures --include-stale` and
apply the task-specific recovery command or lifecycle recovery. The repository
does not define a named supervisor, restart threshold, backoff, or production
service file, so those remain operational choices requiring separate evidence.
