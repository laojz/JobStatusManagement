# REQ-OPS06 Monitoring and Runbook Matrix

**Status:** `Operational Gap`. This matrix documents response behavior, not
monitoring alerts that have fired.

| Signal or condition | First action | Expected result | Stop or escalate when |
|---|---|---|---|
| Readiness unavailable | Check `health`, then inspect process and schema head | Readiness and migration target are explained | Health remains unavailable or targets differ |
| Failed or stale durable work | Run `failures --include-stale`; retry only the named supported record | State is recorded and safe retry is bounded | AgentRun needs unsafe manual retry, or failures recur |
| Startup or restart issue | Confirm one process, run `migrate`, `bootstrap`, `health` | Lifecycle reaches a single `run` process | A second listener or unknown state appears |
| Database integrity or lock issue | Quiesce, run `integrity-check`, preserve evidence | Integrity result is known without overwriting live data | Integrity fails or live data would be replaced |
| Backup or recovery event | Use `backup`, `restore-check`, then the OPS01 isolated sequence | Recovery inputs and counts are comparable | RPO/RTO target is unset or cleanup fails |
| Knowledge index failure | Keep SQLite as source of truth; quiesce before `rebuild-chroma` | Index result and partial failures are recorded | Rebuild reports failure or source data is uncertain |
| IMAP or LLM failure | Preserve bounded failure output and follow provider-specific escalation | No unapproved retry or data disclosure | Credentials, provider ambiguity, or external action is involved |
| Release failure | Follow OPS05 and verify manifest and schema before traffic | Approved version is running or traffic remains stopped | Compatibility is unknown |

## Required evidence fields

For each rehearsal or incident record date/time, commit or artifact,
environment, command or operator action, exit code when applicable, schema
head, counts, cleanup, and limitations. Link the record to the relevant OPS01,
OPS02, OPS04, or OPS05 document.

## Open Decisions

- **Monitoring platform:** `OPEN`, select and approve the alerting and retention target.

## Rehearsal boundary

The runbook, local `health`, failures inspection, retry controls, backup,
restore-check, integrity-check, and quiesced rebuild paths can be rehearsed
locally. Monitoring closure requires a selected platform, alert definitions,
retention, ownership, and a tested alert-to-runbook path. No alert, dashboard,
incident, or drill result is claimed. QQ is excluded, with no live QQ provider
steps.
