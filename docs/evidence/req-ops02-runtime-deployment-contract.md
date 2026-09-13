# REQ-OPS02 Runtime and Deployment Contract

**Status:** `Operational Gap`. No supervisor or deployment artifact is claimed
to exist in this repository.

## Required contract

The deployment must install one versioned application in a declared working
directory, load an approved environment source, and run one process. Its order
is `migrate`, `bootstrap`, `health`, then `run`. Starlette owns the only
listener. The supervisor must forward SIGTERM, stop workers before owned
resources, restart on failure according to an approved policy, and retain logs
without secrets. Two processes must never share the SQLite database.

The contract must define startup failure behavior, process identity, listener
ownership, log location, file permissions, stop timeout, and restart policy.
`health` proves database and migration readiness only. It does not prove QQ or
other external reachability.

## Required evidence fields

For install, start, health, SIGTERM, restart, and failure cases record date/time,
commit or artifact, environment, command, exit code, schema head, counts when
applicable, cleanup, and limitations. Bind the record to the deployment
artifact and its approved configuration template.

## Open Decisions

- **Supervisor target:** `OPEN`, select and approve the service manager and unit format.

## Rehearsal boundary

The lifecycle commands and a local single-process start and stop can be
rehearsed locally. A deployment control cannot close until a supervisor target,
versioned artifact, restart policy, and approved environment are selected and
executed. No supervisor unit, deployment run, or production readiness is
claimed. QQ is excluded, with no live QQ steps.
