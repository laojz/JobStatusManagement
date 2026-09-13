# REQ-E02 IMAP Business-Path Evidence

## Scope and status

This record covers the credential-free, non-QQ deterministic IMAP business path
on the 2026-09-13 worktree. It uses the existing `FakeIMAPGateway`, temporary
SQLite fixture, `FakeLLM`, and `FakeQQGateway` seams. It does not start an IMAP
server, call QQ, use a QQ credential, or exercise the QQ webhook.

REQ-E02 remains **Evidence Required**. This record closes the missing local
business-path evidence for the exercised fake scenario only. It does not prove
QQ provider compatibility or production readiness.

## Scenario evidence

The integration test
`test_non_qq_imap_business_path_is_deterministic_and_recoverable` demonstrates:

- first poll establishes and persists an explicit baseline cursor;
- the next poll receives two provider-neutral envelopes and persists both;
- the following poll supplies the same messages and creates no duplicate mail;
- the stored cursor is passed to each continuation poll;
- a retryable analysis failure persists `RETRY_WAIT` without creating analysis;
- after the deterministic clock advances, analysis is retried and persisted;
- the analyzed result routes to one notification and one fake QQ push;
- the informational message does not trigger LLM analysis;
- persisted mail, analysis, notification, and processed-event counts remain
  idempotent after the complete path.

IMAP adapter cleanup is separately covered by the existing controlled connection
tests in `tests/unit/test_qq_imap_gateway.py`, including the assertion that a
successful poll closes the connection. The integration scenario uses the
existing fixture teardown for temporary SQLite disposal; no new cleanup seam was
invented.

## Executed verification

| Command | Exit code | Result |
| --- | ---: | --- |
| `uv run pytest -q tests/integration/test_mail_pipeline.py tests/integration/test_non_qq_imap_e2e.py tests/unit/test_qq_imap_gateway.py` | 0 | PASS: 30 passed |
| `uv run ruff check tests/integration/test_mail_pipeline.py` | 0 | PASS |
| `uv run ruff format --check tests/integration/test_mail_pipeline.py` | 0 | PASS |
| `uv run basedpyright tests/integration/test_mail_pipeline.py` | 0 | PASS |
| `git diff --check` | 0 | PASS |

The command results above are intentionally limited to this record's test and
quality surface. The repository schema is unchanged; the applicable existing
schema head remains `0008_qq_reply_targets (head)`.

## Provider boundary and next step

The existing limited real QQ IMAP smoke is recorded separately as covering only
real-adapter login, poll, and cleanup. It is not merged with this fake evidence
and is not a complete business-path E2E. No real QQ IMAP business-path E2E was
run in this task because credentials and external provider calls are excluded.

Therefore provider compatibility remains **Evidence Required**. The next step
requires an approved provider test window, sanitized command/result capture,
and an explicit comparison of provider behavior with this fake scenario. Until
then, this evidence must not be described as production readiness or general
IMAP compatibility.
