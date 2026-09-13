# REQ-OPS05 Rollback Procedure

**Status:** `Operational Gap`. This procedure defines controls; it does not
claim that rollback has been executed.

## Procedure

1. Stop or quiesce the single process and preserve the current version,
   schema head, failure output from `failures --include-stale`, and database
   backup.
2. Select an approved older artifact and confirm its manifest, commit, lock
   data, and schema compatibility. If incompatible, stop before accepting
   traffic. Do not edit code or apply an unapproved downgrade.
3. Use the approved application rollback or isolated database restore. Run
   `restore-check`, `integrity-check`, `migrate` only if the approved path calls
   for it, then `bootstrap`, `health`, and `run` in the documented lifecycle.
4. Recheck durable task state with `failures --include-stale`, representative
   counts, schema head, and Chroma state. Rebuild Chroma only while quiesced.
5. Remove temporary files and record the decision, stop conditions, and owner.

## Required evidence fields

Record date/time, commit or artifact before and after, environment, every
command, exit code, schema head, counts, cleanup, limitations, database
snapshot, and actual elapsed time. Record RPO/RTO impact only against approved
numeric targets.

## Open Decisions

- **Numeric RPO/RTO used for rollback impact:** `OPEN`, approval required.

## Rehearsal boundary

The decision checklist, local artifact comparison, `failures --include-stale`,
backup, `restore-check`, integrity checks, and isolated recovery can be
rehearsed locally. Closure requires two approved versioned artifacts, a target
schema policy, and an executed rollback or isolated restore drill. No rollback
execution result is invented. QQ is excluded, with no live QQ actions.
