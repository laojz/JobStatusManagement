# Evidence Index

This index is the reconciliation point for REQ-E01. It describes the current
evidence boundary without turning historical phase records into current facts.
It does not establish production readiness, live QQ validation, or complete
provider compatibility.

## Current records

| Record | Scope | Date | Status |
|---|---|---|---|
| [current-baseline.md](current-baseline.md) | Pre-fix and current controlled worktree baseline and database target | 2026-09-13 | Reconciled; release-artifact binding remains open |
| [requirements-matrix.md](requirements-matrix.md) | All `REQ-*` and `QQ-EX-*` IDs, original status and priority | 2026-09-13 | Reconciled |
| [req-f01-pending-action-routing.md](req-f01-pending-action-routing.md) | Knowledge/mail PendingAction routing and replay | 2026-09-13 | Local implementation verified |
| [req-f02-webhook-ack.md](req-f02-webhook-ack.md) | Durable receipt, non-blocking ACK, and handler lifecycle | 2026-09-13 | Local deterministic contract verified |
| [req-f03-reply-target.md](req-f03-reply-target.md) | Passive/proactive ReplyTarget validation and reconstruction | 2026-09-13 | Local deterministic contract verified |
| [req-e02-imap-business-path.md](req-e02-imap-business-path.md) | Credential-free non-QQ IMAP business-path fake E2E | 2026-09-13 | Local path recorded; provider compatibility remains Evidence Required |
| [req-e03-composite-agent-run.md](req-e03-composite-agent-run.md) | Credential-free non-QQ composite AgentRun fake E2E | 2026-09-13 | Local path recorded; real provider business E2E remains Evidence Required |
| [req-ops01-recovery-drill.md](req-ops01-recovery-drill.md) | Recovery drill checklist and local execution record | 2026-09-13 | Local isolated drill passed; numeric RPO/RTO approval and full operational closure open |
| [req-ops02-runtime-deployment-contract.md](req-ops02-runtime-deployment-contract.md) | Runtime, deployment, and supervisor contract | 2026-09-13 | Control defined; supervisor target and artifact execution open |
| [req-ops03-ci-quality-evidence-contract.md](req-ops03-ci-quality-evidence-contract.md) | CI quality gates and evidence contract | 2026-09-13 | Control defined; CI provider and retained run open |
| [req-ops04-release-manifest-contract.md](req-ops04-release-manifest-contract.md) | Release manifest and source traceability contract | 2026-09-13 | Local typed contract defined and checkable; generated artifact, checksum, and source/build binding evidence open |
| [req-ops05-rollback-procedure.md](req-ops05-rollback-procedure.md) | Rollback procedure and rehearsal boundary | 2026-09-13 | Control defined; approved artifacts and drill open |
| [req-ops06-monitoring-runbook-matrix.md](req-ops06-monitoring-runbook-matrix.md) | Monitoring and operational response matrix | 2026-09-13 | Control defined; monitoring platform and alert drill open |
| [final-gate-2026-09-13.md](final-gate-2026-09-13.md) | Final current-worktree non-QQ gate results and blockers | 2026-09-13 | Local evidence recorded; external gates remain open |
| [deployed-functional-flow-2026-09-14.md](deployed-functional-flow-2026-09-14.md) | Deployed public surface and complete functional-flow validation procedure/execution record | 2026-09-14 | Procedure recorded; fresh live execution required |

## Evidence classes

- **Local deterministic contract**: tests using temporary SQLite, fake gateway,
  fake transport, or local Starlette surfaces. This includes QQ ACK and
  `ReplyTarget` contracts. It is not live QQ validation.
- **Fake or `MockTransport`**: deterministic seams such as
  `httpx2.MockTransport` and fake LLM or gateway implementations. These prove
  code contracts only.
- **Limited smoke**: the recorded IMAP login, poll, cleanup path and synthetic
  real LLM request and response parsing path. These do not prove full provider
  compatibility.
- **Non-QQ E2E gap**: provider compatibility and approved real-provider
  business evidence remain open even when a local fake E2E record exists.
- **QQ-Excluded/Blocked**: live QQ credentials, public webhook, real message
  exchange, file or image capability, ambiguous provider outcomes, and online
  restart recovery. These are registered, not executed.

## Historical records

Phase documents preserve their original snapshots, including figures such as
`120 tests`, `337 passed`, and `58 passed`. Those records remain historical and
must not replace the current baseline. The 2026-09-13 result of
`373 passed + 2 failed` is the separately labeled pre-fix history. The current
controlled worktree result is `404 passed`, with release-artifact and CI binding
still open.

The authoritative local-progress and remaining-gates reconciliation is in the
[requirements agent input](../non-qq-production-readiness-requirements-agent-input.zh-CN.md#42-当前处理状态对账).

## Evidence field rule

Any new evidence record must include: date, commit or artifact, environment,
command, exit code, result, schema head, cleanup, and limitations. No commit
hash, artifact checksum, CI provider, supervisor, RPO/RTO value, or provider
E2E result is inferred when it is not recorded.
