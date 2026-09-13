# REQ-OPS04 Release Manifest Contract

**Status:** Local typed contract defined; `Operational Gap` remains for a
generated release artifact, checksum, and source/build binding.

## Required manifest

Each release artifact must have a unique application version, source commit,
Python and uv versions, SDK versions, lockfile digest, schema head, build
date/time, artifact checksum, evidence index, and runbook version. The local
contract is implemented by the frozen Pydantic `ReleaseManifest` model in
`src/jobs_status_manager/release_manifest.py`. Its validator rejects missing or
blank provenance values and compares `schema_head` with an explicitly supplied
expected migration head.

A future build must populate the model from the resolved application metadata,
source revision, interpreter and uv versions, installed SDK metadata, lockfile
digest, migration head, build clock, produced artifact checksum, evidence-index
reference, and runbook version. This module only parses and validates those
inputs; it does not build, publish, or claim an artifact. Before start, compare
the populated manifest with the installed source, lock file, migrations, and
`health` result. A version or schema mismatch stops deployment.

## Required evidence fields

Record date/time, commit or artifact, environment, command, exit code, schema
head, counts when checked, cleanup, and limitations. Do not replace a missing
checksum, artifact ID, or provider record with an inference.

## Rehearsal boundary

Manifest fields and local consistency checks can be verified without external
services. Operational closure still requires an approved generated artifact,
its recorded checksum, source/build binding, artifact-store evidence, and a
clean installation check. No release or rollback execution result is claimed.
QQ is excluded and must not appear as a release validation step.
