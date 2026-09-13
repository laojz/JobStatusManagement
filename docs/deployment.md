# Deployment

Run exactly one process for one user, mailbox, and QQ account. SQLite WAL, local
Chroma, uploads, and the database are local durable state. Do not run a second
process against the same database, and do not start a second server for QQ.
Starlette owns the only listener, including `POST /webhooks/qq`; botpy is an
in-process client and transport, not another HTTP service.

## Configuration and credential gates

The `.env.example` file is safe and credential-free. These settings control the
single-process runtime:

| Variable | Required | Meaning |
| --- | --- | --- |
| `APP_DATABASE_PATH` | yes | SQLite database path |
| `APP_DATA_DIR` | yes | private uploads and application data directory |
| `APP_HOST` / `APP_PORT` | yes | the one Starlette listener; default `127.0.0.1:8000` |
| `APP_QQ_USER_OPENID` | yes | configured single-user QQ binding |
| `APP_QQ_ENABLED` | no | explicit production botpy gate; default `false` |
| `APP_QQ_APP_ID` | no | botpy production credential pair, only with `APP_QQ_APP_SECRET` |
| `APP_QQ_APP_SECRET` | no | secret half of the botpy production credential pair |
| `APP_QQ_API_BASE_URL` | yes | current QQ API base, default `https://api.bot.qq.com` |
| `APP_QQ_TOKEN_BASE_URL` | conditional | explicit operator-verified QQ token base required when QQ credentials are enabled |
| `APP_QQ_WEBHOOK_PATH` | yes | Starlette webhook route path; default `/webhooks/qq` |
| `APP_QQ_WEBHOOK_TOKEN` | no | local/test webhook token when using the fake gateway |
| `APP_QQ_REQUEST_TIMEOUT_SECONDS` | yes | bounded SDK request timeout |
| `APP_QQ_DISPATCH_TIMESTAMP_MAX_AGE_SECONDS` | yes | maximum accepted dispatch signature age in either direction; `1..3600`, default `300` |
| `APP_QQ_DOWNLOAD_TIMEOUT_SECONDS` / `APP_QQ_DOWNLOAD_DEADLINE_SECONDS` | yes | bounded inbound attachment time limits |
| `APP_QQ_MAX_DOWNLOAD_BYTES` | yes | inbound attachment size limit |
| `APP_LLM_API_KEY` | no | optional LLM integration credential |
| `APP_EMBEDDING_API_KEY` + `APP_CHROMA_PATH` | pair | optional production indexing; configure both or neither |

QQ production registration requires explicit `APP_QQ_ENABLED=true` and complete
credentials. With the gate false, even complete credentials remain in no-QQ
mode and botpy is not constructed. With the gate true and neither
`APP_QQ_APP_ID` nor `APP_QQ_APP_SECRET`, the runtime remains in no-QQ mode and
does not instantiate botpy. With both, the Starlette lifespan creates exactly
one botpy `Client`, one custom transport, and one gateway, then injects that
gateway into notification and agent workers. Supplying `LifecycleAdapters.qq`
is authoritative and prevents any automatic or injected botpy runtime from
being created. Supplying only one production credential fails settings loading
with a safe field-level configuration error; no secret value is included.
Complete QQ credentials without `APP_QQ_TOKEN_BASE_URL` also fail before
startup. Both QQ API and token URLs must use HTTPS. `https://bots.qq.com` is retained only as an explicitly supplied,
unverified SDK-era compatibility fallback; it is never a production default.

## Startup, readiness, liveness, and shutdown

```bash
uv sync
cp .env.example .env
chmod 600 .env
chmod 700 ./data
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager health
uv run python -m jobs_status_manager run
```

The application CLI reads `APP_DATABASE_PATH` from the selected `.env` or the
process environment. Direct Alembic commands do not read `.env`; provide the
same path explicitly for every migration operation:

```bash
APP_DATABASE_PATH=./data/jobs_status.db uv run alembic check
APP_DATABASE_PATH=./data/jobs_status.db uv run alembic current
APP_DATABASE_PATH=./data/jobs_status.db uv run alembic upgrade head
```

Alembic resolves a relative `APP_DATABASE_PATH` from the command's working
directory. It has no `jobs_status_alembic.db` fallback, so omitting the variable
fails instead of creating a side database.

Use a supervisor that runs one private process, forwards SIGTERM, and restarts on failure. Keep `.env`, database/WAL files, Chroma, uploads, and backups private. Do not commit mailbox passwords, QQ credentials, LLM keys, or backup files.

```bash
curl --fail http://127.0.0.1:8000/health
uv run python -m jobs_status_manager failures --include-stale
mkdir -p ./backups
uv run python -m jobs_status_manager backup ./backups/jobs-$(date +%Y%m%d-%H%M%S).db
uv run python -m jobs_status_manager restore-check ./backups/jobs-YYYYMMDD-HHMMSS.db
uv run python -m jobs_status_manager integrity-check
```

`GET /live` is the liveness check. It returns HTTP 200 with `{"status":"alive"}`
when the Starlette process can answer and does not query SQLite, migrations,
durable tasks, IMAP, LLM, QQ, Chroma, or any other external capability. A
failed `/live` probe can be used as evidence of a process-level failure; a
dependency failure must not be converted into a liveness failure.

`GET /health` is the readiness check. It returns HTTP 200 only after the
database connection succeeds, the schema revision is exactly
`0008_qq_reply_targets`, and configured product capabilities are ready (or the
runtime is explicitly local-only). HTTP 503 means the process is not ready.
Missing schema is reported as `database: "not_ready"`; a database read failure
is reported as `database: "unavailable"`. A non-head schema reports its
observed `schema_version` but is not ready. The existing `/health` JSON field
set is stable and includes database, schema, external capability, and durable
task counters. It does not prove QQ credentials or external endpoint
reachability.

`durable_tasks: "degraded"` means the readiness query found one or more failed
or stale persisted tasks. It does not change an otherwise-ready `/health` HTTP
200 into HTTP 503 and does not by itself justify a process restart. Operators
must inspect the bounded task list and apply the task-specific recovery
procedure in [Operations](operations.md). This is distinct from `/live`: a
healthy process can be ready while durable work needs attention.

| Condition | `/live` | `/health` | Supervisor/operator action |
| --- | ---: | ---: | --- |
| Process answers; head schema and local-only or configured capabilities are ready | 200 | 200 | Keep one process running; admit work according to the configured mode. |
| Process answers; database cannot be read | 200 | 503, `database=unavailable` | Do not route work; inspect logs, path, permissions, and locks before repair. Do not start a second process. |
| Process answers; schema is missing or below `0008_qq_reply_targets` | 200 | 503, `database=not_ready` | Keep out of service; run migration/current checks against the same `APP_DATABASE_PATH`. |
| Local runtime has IMAP and LLM disabled | 200 | 200, `product_readiness=local_only` | Keep running as local-only; do not call this production provider evidence. |
| Configured capability is absent or construction failed | 200 | 503, `product_readiness=not_ready` | Keep out of production traffic; correct configuration or adapter wiring, then restart under the approved process policy. |
| Failed or stale durable tasks, while base readiness is healthy | 200 | 200, `durable_tasks=degraded` | Do not restart solely from this field; run `failures --include-stale` and recover the identified task. |

The repository does not prescribe a named supervisor, restart threshold, backoff,
or production service file. Any supervisor integration must preserve one
process per database, forward SIGTERM, use `/live` for process liveness, and
use `/health` for traffic readiness. Local HTTP tests and fake adapters are
not live QQ or provider validation.

The application starts worker tasks only after startup recovery and botpy
transport readiness. On shutdown, worker tasks are cancelled first, then the
lifecycle closes the botpy client, closes the Starlette transport, and disposes
the database. Each owned resource is closed once.

Startup scans stale Notification `SENDING`, PendingAction `EXECUTING`, AgentRun
`RUNNING`, and Knowledge `INDEXING` before worker loops. AnyIO structured
shutdown leaves durable windows retryable or explicitly failed. Delivery is
at-least-once; exactly-once is not claimed. Configure
`APP_EMBEDDING_API_KEY` and `APP_CHROMA_PATH` together for Bailian embeddings
and local Chroma. Stop or quiesce the runtime before `rebuild-chroma`.

## Safe local smoke

These commands make no QQ request and do not require secrets. The effective
listener bind is `APP_HOST`/`APP_PORT`; `APP_QQ_WEBHOOK_PATH` controls only the
Starlette route path.

```bash
cp .env.example .env
chmod 600 .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager demo-mail-pipeline
uv run python -m jobs_status_manager health
uv run pytest -q tests/unit/test_settings.py tests/unit/test_factory.py tests/integration/test_qq_webhook.py tests/e2e/test_health.py
```

Do not set only one of `APP_QQ_APP_ID` and `APP_QQ_APP_SECRET` while testing a
local process. To exercise that failure without a secret, construct settings
with one placeholder and confirm the safe `must be configured together`
configuration error in `tests/unit/test_settings.py`.

## Human-gated live QQ boundary

Live validation is not part of local CI and was not run by this change. Every
step below is `BLOCKED` until a human operator explicitly approves a
non-destructive smoke window, supplies credentials through an ignored
environment file or secret manager, approves the exact recipient and endpoint
values, and approves rollback and cleanup. Never store secrets, signatures,
complete openids, provider payloads, attachment contents, or URL query values
in logs or evidence.

Before startup, the operator must approve the test account, exactly one test
recipient, non-sensitive test messages/files, expected side effects, stop
conditions, credential injection method, and cleanup plan. Use one process,
one database, one Starlette listener, one botpy client, and one custom
transport. Do not start botpy's own HTTP server or a second listener.

Only after those approvals are recorded outside the evidence file may the
operator inject values such as:

```bash
export APP_QQ_APP_ID='<provided-by-operator>'
export APP_QQ_APP_SECRET='<provided-by-operator>'
export APP_QQ_USER_OPENID='<approved-test-recipient>'
export APP_QQ_API_BASE_URL='https://api.bot.qq.com'
export APP_QQ_TOKEN_BASE_URL='<operator-verified-token-endpoint>'
uv run python -m jobs_status_manager run
curl --fail http://127.0.0.1:8000/health
```

### Ordered live checklist

1. Confirm the API endpoint and Token endpoint are current, operator-verified,
   and explicitly authorized. Record hostnames only; do not assume SDK defaults
   or silently use `https://bots.qq.com`.
2. Run `migrate`, `bootstrap`, and `health`; confirm readiness before any QQ
   event. Configuration output must not contain secrets.
3. Verify API access, token acquisition, and token refresh using the authorized
   endpoints. Record HTTP status, QQ `err_code`, and redacted trace/provider
   IDs only.
4. Send the signed URL challenge (`op=13`) to `POST /webhooks/qq`; confirm the
   challenge response and mark `PASS`, `FAIL`, or `BLOCKED`.
5. Receive one non-destructive C2C text event; confirm persistence precedes the
   ordinary ACK and record only redacted event/message IDs.
6. Re-deliver that exact event; confirm duplicate handling creates no second
   message, run, or file.
7. Exercise one passive text reply using the persisted openid, message ID,
   event ID, and sequence where available; confirm the approved recipient and
   record only status/provider ID.
8. Exercise one proactive C2C push to the approved recipient without
   passive-only fields; record only status/provider ID.
9. Receive one bounded, non-sensitive attachment through the URL metadata
   path; confirm validation, storage, and cleanup without recording the URL,
   query, token, or file contents.
10. Send one ordinary file and, only if separately approved, one image using a
    non-sensitive fixture. Mark capability `PASS`, `FAIL`, or `BLOCKED` from
    the provider result; SDK method presence is not proof of support.
11. Exercise one approved ambiguous proactive outcome, such as a timeout or
    connection interruption. Confirm it is recorded as ambiguous and is not
    blindly retried or duplicated. Stop if delivery or recipient is unclear.
12. Stop with Ctrl-C or SIGTERM, confirm client/transport/task shutdown, then
    restart once with the same database and verify durable recovery/readiness
    without duplicating prior events or sends.
13. Remove approved test messages/files, temporary uploads, logs, and test
    database artifacts. Confirm no credentials, `.part` files, listeners, or
    provider payloads remain.

Record each step as `PASS`, `FAIL`, `BLOCKED`, or `NOT_RUN` with timestamp,
code/SDK version, authorized endpoint hostnames, HTTP status, QQ `err_code`,
redacted correlation/provider IDs, observed side effects, and cleanup result.
Immediately stop and mark remaining steps `BLOCKED` for an unexpected
recipient, unexpected endpoint, credential leak, unsafe file response,
ambiguous side effect, or failed cleanup. Ordinary-file support and endpoint
compatibility remain unclaimed until this human session is completed and
reviewed.
