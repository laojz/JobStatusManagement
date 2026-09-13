# Requirements Matrix

This matrix preserves the IDs, statuses, and priorities from the authoritative
requirements input. `Evidence state` describes this reconciliation record, not
an implementation closure decision.

See the authoritative [current processing reconciliation](../non-qq-production-readiness-requirements-agent-input.zh-CN.md#42-当前处理状态对账) for the local-progress and remaining-gates summary.

| ID | Original status | Priority | Evidence state on 2026-09-13 |
|---|---|---|---|
| REQ-F01 | `Confirmed Defect` | `P0` | Local implementation and regression evidence recorded in [req-f01-pending-action-routing.md](req-f01-pending-action-routing.md); live QQ excluded |
| REQ-F02 | `Confirmed Defect` | `P0` | Local deterministic ACK evidence recorded in [req-f02-webhook-ack.md](req-f02-webhook-ack.md); live QQ excluded |
| REQ-F03 | `Confirmed Defect` | `P0` | Local deterministic ReplyTarget evidence recorded in [req-f03-reply-target.md](req-f03-reply-target.md); live QQ excluded |
| REQ-CFG01 | `Confirmed Defect` | `P0` | Fixed and verified by current `404 passed` controlled suite; pre-fix baseline retained separately |
| REQ-CFG02 | `Operational Gap` | `P0` | Same temporary target verified by upgrade, check, current, and CLI health; no side database created |
| REQ-CFG03 | `Required Control` | `P0` | `/live` and `/health` local HTTP matrix implemented and recorded; supervisor artifact/drill and live-provider evidence remain open |
| REQ-E01 | `Evidence Required` | `P1` | Current baseline, index, and matrix reconciled; release-artifact binding remains open |
| REQ-E02 | `Evidence Required` | `P1` | Credential-free fake business-path E2E recorded; real provider compatibility and approved provider E2E remain open |
| REQ-E03 | `Evidence Required` | `P1` | Fake composite AgentRun E2E recorded; MockTransport and synthetic smoke remain limited, real-provider business E2E open |
| REQ-OPS01 | `Operational Gap` | `P0` | [Local isolated recovery drill](req-ops01-recovery-drill.md) passed with schema/count/rebuild/cleanup evidence; approved numeric RPO/RTO and full operational closure remain open |
| REQ-OPS02 | `Operational Gap` | `P1` | [Runtime and deployment contract](req-ops02-runtime-deployment-contract.md) defined; supervisor target and artifact execution open |
| REQ-OPS03 | `Operational Gap` | `P1` | [CI quality and evidence contract](req-ops03-ci-quality-evidence-contract.md) defined; provider and retained CI run open |
| REQ-OPS04 | `Operational Gap` | `P1` | [Release manifest contract](req-ops04-release-manifest-contract.md) typed and locally checkable; generated artifact, checksum, and source/build binding remain open |
| REQ-OPS05 | `Operational Gap` | `P1` | [Rollback procedure](req-ops05-rollback-procedure.md) defined; approved artifacts and drill evidence open |
| REQ-OPS06 | `Operational Gap` | `P1` | [Monitoring and runbook matrix](req-ops06-monitoring-runbook-matrix.md) defined; platform and alert drill evidence open |
| QQ-EX-01 | `QQ-Excluded/Blocked` | Not assigned in source table | Public QQ webhook and server binding not executed |
| QQ-EX-02 | `QQ-Excluded/Blocked` | Not assigned in source table | Real QQ URL challenge not executed |
| QQ-EX-03 | `QQ-Excluded/Blocked` | Not assigned in source table | Real C2C receive, passive reply, and proactive push not executed |
| QQ-EX-04 | `QQ-Excluded/Blocked` | Not assigned in source table | Real QQ attachment, file, and image capability not executed |
| QQ-EX-05 | `QQ-Excluded/Blocked` | Not assigned in source table | Online ambiguous-outcome behavior not executed |
| QQ-EX-06 | `QQ-Excluded/Blocked` | Not assigned in source table | Online QQ restart and session recovery not executed |

The source exclusion table assigns statuses but no priorities to `QQ-EX-01`
through `QQ-EX-06`; this matrix therefore records that absence rather than
inventing priorities. None of the exclusion items is `PASS`.

## Evidence boundary

Local fake transport, fake gateway, `MockTransport`, temporary SQLite, and
local HTTP tests remain deterministic contract evidence. They do not establish
live QQ behavior or provider compatibility. The recorded IMAP and LLM smokes
are limited paths. Historical phase counts remain historical. Unexecuted
provider E2E, deployment, recovery, CI artifact, rollback, and monitoring
evidence remains open.

REQ-CFG03 evidence is limited to the local Starlette HTTP surface. The
contract is: `/live` returns HTTP 200 without querying dependencies; `/health`
returns HTTP 200 only for a readable head-schema database plus ready or
local-only capability state; missing/unavailable database or non-head schema
returns HTTP 503; and failed or stale durable tasks return HTTP 200 with
`durable_tasks=degraded` when base readiness remains healthy. No named
supervisor, restart threshold, provider probe, credential, or production
deployment behavior is inferred from these tests.
