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
Phase 6 acceptance passed on 2026-09-11 with 120 tests, clean Ruff, format,
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

## Current QQ Adapter Acceptance Snapshot

本轮确定性验收（2026-09-12）已完成 QQ botpy 适配器的本地实现验证：

```text
qq-botpy-sdk==2.0.4
uv run pytest -q                         267 passed in 8.28s
uv run ruff check .                      PASS
uv run ruff format --check .             PASS (166 files)
uv run basedpyright                      0 errors, 0 warnings, 0 notes
uv lock --check                          PASS
uv run alembic check                     PASS
git diff --check                         PASS
current database head                    0008_qq_reply_targets (head)
```

同时验证了 webhook durable ACK、事件幂等、C2C reply target 持久化重建、
出站结果分类、附件 SSRF/重定向/deadline/文件头校验、`.part` 清理和安全错误
脱敏。当前本地 `.env` 已确认包含 App ID、App Secret、测试 user openid 和
显式 Token URL，但 `APP_QQ_ENABLED=false`，因此本地运行不会启动真实 botpy。

以下项目仍未通过真实 QQ 平台验收，必须保持 `BLOCKED`：API/token endpoint
访问与刷新、URL challenge、C2C 接收/回复、主动推送、附件接收下载、普通
文件/图片发送、ambiguous outcome 处理和真实重启恢复。本轮没有发起真实 QQ
endpoint 请求，也没有宣称生产兼容性。完整证据见
`.omo/evidence/task-8-qq-botpy-sdk-adapter.md`，实现基线见
[`docs/architecture.md`](docs/architecture.md) 和
[`docs/qq-botpy-adapter-design.md`](docs/qq-botpy-adapter-design.md)。

Run the complete verification suite with:

```bash
uv run pytest -q
```
