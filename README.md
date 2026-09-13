# Jobs Status Manager

This repository contains the Phase 0 foundation through the Phase 6 reliability
and operations workflow: a single-process Python application with safe configuration, SQLite
WAL migrations, identity bootstrap, deterministic PendingAction confirmation,
atomic business writes, QQ file intake, and a rebuildable Chroma index.

## Quick start

```bash
uv sync
cp .env.example .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run python -m jobs_status_manager run
```

Then query `http://127.0.0.1:8000/health`. Stop the process with `Ctrl-C`.
The runtime is single-process: Starlette owns the only listener, including
`POST /webhooks/qq`; do not start a second botpy or application server. QQ
botpy is created only when `APP_QQ_ENABLED=true`, both `APP_QQ_APP_ID` and
`APP_QQ_APP_SECRET` are configured, and `APP_QQ_TOKEN_BASE_URL` is explicitly
supplied. The SDK-era
`https://bots.qq.com` value is not a production default. See
[`docs/deployment.md`](docs/deployment.md) for the credential matrix, readiness
semantics, shutdown order, and human-gated live smoke.

Phase 1 adds only Application, JobEvent, PendingAction, and OutboxEvent. Run
the local vertical-loop demo with:

```bash
uv run python -m jobs_status_manager demo-status-core
```

Run the credential-free Phase 2 fake pipeline with:

```bash
uv run python -m jobs_status_manager demo-mail-pipeline
```

Phase 3 adds a token-protected QQ webhook, durable idempotent conversation
intake, sequential Application/Mail tools, bounded AgentRun execution, and
fake QQ delivery. Phase 4 adds the confirmation-gated
`UpdateApplicationStatus` write tool. Phase 5 adds `.txt`, `.md`, `.markdown`,
`.pdf`, and `.docx` upload parsing, confirmation-gated `AddKnowledge` and
`RemoveKnowledge`, deterministic chunks, injected Embedding/Chroma workers,
and `SearchKnowledge`. SQLite remains the knowledge source of truth while
Chroma is a rebuildable local index. Configure `APP_EMBEDDING_API_KEY` and
`APP_CHROMA_PATH` together to enable Alibaba Cloud Bailian indexing and the
stopped-process `rebuild-chroma` command.

## Acceptance status

The current evidence reconciliation is maintained in
[`docs/evidence/index.md`](docs/evidence/index.md). The authoritative
pre-fix baseline on 2026-09-13 was `373 passed + 2 failed`; later results must
be labeled with their execution context. Historical phase counts below are
snapshots and do not replace that baseline.

Phase 0 acceptance passed on 2026-09-09 before Phase 1 work began. Its
historical result was 10 passing tests, clean Ruff and basedpyright checks,
and Alembic revision `0001_identity (head)`. Phase 1 and Phase 2 were also
accepted. Phase 3 acceptance passed on 2026-09-10. Phase 4 acceptance passed on
the same day with 54 passing tests. Phase 5 core implementation verification
passed with 58 tests, clean Ruff, format, basedpyright, and source-policy
checks, plus a successful `0006 -> 0005 -> 0006` Alembic round trip. Full Phase
5 acceptance remains partial: the credential-free real `LocalChroma` temporary-path
rebuild/search drill and the real Bailian provider smoke passed. The complete QQ file-to-RAG E2E
and the three-tool composite Agent scenario remain pending.
Phase 6 acceptance passed on 2026-09-11 with a historical snapshot of 120 tests, clean Ruff, format,
basedpyright, and Alembic checks, a real Bailian `text-embedding-v4` 1024-dimensional
finite-vector smoke, isolated CLI rebuild counts, and successful matching retrieval.
The current database revision is `0008_qq_reply_targets (head)`.
The credential-free mail demo also passed with one outbound fake QQ push and
no automatic application change. See
[`docs/phase-0-verification.md`](docs/phase-0-verification.md) for the Phase 0
record, [`docs/phase-1-verification.md`](docs/phase-1-verification.md) for the
Phase 1 implementation and current verification, and
[`docs/phase-2-verification.md`](docs/phase-2-verification.md) for the Phase 2
acceptance record and scope boundary, and
[`docs/phase-3-verification.md`](docs/phase-3-verification.md) for Phase 3.
Phase 4 details are recorded in
[`docs/phase-4-verification.md`](docs/phase-4-verification.md), and Phase 5 in
[`docs/phase-5-verification.md`](docs/phase-5-verification.md).
Phase 6 operations and deployment are documented in
[`docs/operations.md`](docs/operations.md), [`docs/deployment.md`](docs/deployment.md),
and [`docs/phase-6-verification.md`](docs/phase-6-verification.md).

## Current IMAP and LLM Adapter Acceptance Snapshot

本轮验收（2026-09-13）完成了 QQ IMAP、OpenAI-compatible
`deepseek-flash` LLM 以及生命周期/readiness 接线的有限路径验证：

```text
qq-botpy-sdk==2.0.4
uv run pytest -q tests/unit/test_openai_compatible_llm.py
                                           PASS: 31 passed
uv run pytest -q                         PRE-FIX BASELINE: 373 passed; 2 readiness assertion failures; CURRENT CONTROLLED WORKTREE: 404 passed
uv run ruff check .                      PASS
uv run ruff format --check .             PASS (206 files)
uv run basedpyright                      0 errors, 0 warnings, 0 notes
uv lock --check                          PASS
uv run alembic check                     PASS (no new upgrade operations detected)
uv run alembic current                   PASS: 0008_qq_reply_targets (head)
git diff --check                         PASS
schema migration added by this work      none
changed Python file LSP diagnostics      PASS: no diagnostics
```

同时验证了 IMAP cursor/MIME/UID poll、LLM strict parsing 和 typed errors、
tool_call_id 透传、LLM client ownership、factory/lifecycle shutdown、health readiness、
durable retry 和现有 QQ webhook/附件安全边界。LLM 邮件分析 prompt 包含生成的 schema，
并执行 strict validation。正常 logout/shutdown 后只忽略 plain `OSError(errno.EBADF)`。

有限 smoke 证据包括：之前执行的真实 QQ IMAP adapter smoke 已通过，覆盖登录、poll
和 cleanup 路径；一次 synthetic-input real LLM smoke 已通过，使用真实 LLM adapter 和
合成邮件输入验证请求、真实 provider 响应解析、contract reason 分类及 strict validation
路径。这些只是有限的已
执行路径结果，不代表完整的 provider compatibility 或 production readiness。

以下项目仍必须保持 `BLOCKED`/human-gated：QQ token/API endpoint、URL challenge、
C2C 收发、主动推送、附件/文件/图片能力、ambiguous outcome 处理和真实在线重启恢复。
完整当前证据见
[`docs/imap-llm-adapter-verification.md`](docs/imap-llm-adapter-verification.md)，
实现基线见 [`docs/imap-llm-adapter-implementation.md`](docs/imap-llm-adapter-implementation.md)
和 [`docs/architecture.md`](docs/architecture.md)。

Run the complete verification suite with:

```bash
uv run pytest -q
```
