# QQ IMAP and OpenAI-Compatible LLM Adapter Verification

## Scope

本记录汇总 2026-09-13 当前工作树对 QQ IMAP、OpenAI-compatible
`deepseek-flash` LLM、Agent contract、factory/lifecycle/readiness 和持久化重试
边界的确定性验收结果。

详细实现契约见
[`imap-llm-adapter-implementation.md`](./imap-llm-adapter-implementation.md)，
总体模块边界见 [`architecture.md`](./architecture.md)。本记录不修改或覆盖
Phase 0 through 6 的历史验收结果。

## Verified Behavior

- QQ IMAP 使用 `imap.qq.com:993`、SSL、readonly `INBOX` 和
  `APP_IMAP_AUTH_CODE`，每次 poll 使用新连接并执行安全 cleanup。正常 logout
  之后的 shutdown 只忽略 plain `OSError(errno.EBADF)`；其他 cleanup failure
  仍会被分类并保留。
- IMAP 使用版本化 `UIDVALIDITY + UID` cursor，支持 initial、legacy、invalid、
  version、UIDVALIDITY change 和 UID regression reset 分类。
- MIME 转换覆盖 bounded body、header/address 解码、charset、plain/HTML 选择、
  附件排除、日期回退和控制字符清洗。
- OpenAI-compatible adapter 固定 `deepseek-flash`，使用显式 HTTPS base URL、
  非流式 Chat Completions、`httpx2`、transport retries `0` 和 ownership-aware close。
- LLM 邮件分析现在发送包含生成的 `JobMailAnalysisInput` schema 的 prompt，分类安全的
  contract reason，处理 `finish_reason`，并使用 strict validation。邮件分析和
  Conversation 采用严格响应校验；Tool schema 从现有 registry 派生，
  `tool_call_id` 在 `ToolCall`、`ToolResult`、`PendingAction` 和 provider tool
  message 之间保留。
- IMAP/LLM adapter 不执行业务重试；邮件分析、Transactional Outbox 和 AgentRun
  的持久化状态机负责 retryable 错误的恢复，确定性配置/认证/请求拒绝错误进入终态。
- factory 按 capability 合并显式注入和新建资源；显式注入资源不由 factory 关闭。
- health 不调用外部服务；local-only、production ready/not-ready 和 fake adapter
  状态均通过本地 readiness 计算。
- LLM 分析 provenance 记录实际 `deepseek-flash` model identity；测试 fake 保留
  `fake` identity 默认。

## Live Smoke Evidence

以下是有限的 live smoke 证据，不代表完整的 production readiness：

- 之前执行的真实 QQ IMAP adapter smoke 已通过，覆盖真实 IMAP adapter 的登录、poll
  和 cleanup 路径。
- 一次 synthetic-input real LLM smoke 已通过，使用真实 LLM adapter 和合成邮件输入验证
  请求、真实 provider 响应解析、contract reason 分类以及 strict validation 路径。

smoke 过程中没有在本记录中保存 secret、邮件正文、prompt、provider payload、完整响应、
provider ID 或 Authorization 数据。

## Automated Verification

| Command | Result |
| --- | --- |
| `uv run pytest -q tests/unit/test_openai_compatible_llm.py` | PASS: 31 passed |
| `uv run pytest -q` | 373 passed; 2 pre-existing readiness assertion failures, unrelated to the adapter fixes |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS: 180 files already formatted |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS: No new upgrade operations detected |
| `uv run alembic current` | PASS: `0008_qq_reply_targets (head)` |
| `uv lock --check` | PASS |
| `git diff --check` | PASS |
| changed Python file LSP diagnostics | PASS: no diagnostics |

## Database Boundary

本轮 IMAP/LLM adapter、factory、lifecycle 和 health 工作没有新增数据库 migration，
没有改变现有 schema head。当前 repository head 是
`0008_qq_reply_targets (head)`；该 revision 属于此前的 QQ reply-target 持久化工作，
不是本轮 adapter 新建的 migration。

## Deterministic Acceptance Boundary

本轮确定性证据来自临时 SQLite、fake gateway、`httpx2.MockTransport`、单元测试、
集成测试和本地 Starlette health surface。已验证的是代码契约、事务边界、错误分类、
资源所有权、幂等和 readiness 逻辑。Live smoke 只补充了上面明确列出的有限 adapter
路径，不能据此推导完整的第三方账号、公网服务或生产 provider 兼容性。

## Production Readiness / Human-Gated

以下能力仍未完成生产级验证，必须保持 `BLOCKED`/human-gated：

- 真实 QQ token/API endpoint、Webhook challenge、C2C 收发、主动推送和在线重启恢复。
- 真实 QQ 附件下载、文件/图片发送以及 ambiguous provider outcome 处理。
- QQ webhook、delivery、attachment、file/image、ambiguous outcome 和 online restart
  capabilities 的完整端到端场景。

这些项目需要公网环境、经批准的凭据和人工 live smoke。有限的 IMAP 和 synthetic real
LLM adapter smoke evidence 不替代这些检查，也不改变生产 readiness 判断。
