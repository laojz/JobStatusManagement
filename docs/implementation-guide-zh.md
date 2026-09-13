# QQ 邮箱 IMAP 与 DeepSeek LLM 实现执行手册

## 1. 文档定位

本文是后续编码工作的执行手册。它把现有设计决策转换为可分批实施、可测试、可回滚的工程任务，适用于以下两个生产能力：

1. 从 QQ 邮箱 `imap.qq.com:993` 以 SSL 轮询 `INBOX`，把邮件转换为现有 `MailEnvelope` 并可靠入库。
2. 通过显式配置的 OpenAI-compatible Chat Completions 服务调用 `deepseek-flash`，同时支持邮件分析和 Conversation Agent。

本文不是供应商 SDK 使用说明，也不包含真实凭据。`APP_LLM_BASE_URL` 必须由部署操作者提供并验证；不得根据本文猜测或硬编码某个 DeepSeek endpoint。

已有的高层设计仍以 [`imap-llm-adapter-implementation.md`](./imap-llm-adapter-implementation.md) 为准。本文主要补足实施顺序、修改边界、测试要求、失败恢复和发布验收。

## 2. 当前基线

### 2.1 已确认的架构事实

当前应用是单进程 Python 模块化单体：Starlette 持有唯一 HTTP listener，AnyIO 驱动 worker，SQLite 使用 WAL，Chroma 是可重建的本地索引。数据库是业务事实的唯一来源。

关键入口如下：

| 文件 | 当前职责 | 后续工作 |
| --- | --- | --- |
| `src/jobs_status_manager/config/settings.py` | `APP_` 配置、`.env` 加载、未知字段拒绝、秘密字段 | 增加明确的 IMAP/LLM 配置和生产门控 |
| `src/jobs_status_manager/mail.py` | `MailEnvelope`、邮件分类、分析输入、提示构造 | 增加 poll 批次结果；删除重复 Protocol |
| `src/jobs_status_manager/mail_poller.py` | 读取 cursor、事务外 poll、逐封幂等入库、推进 cursor | 使用 Adapter 返回的显式 `next_cursor` |
| `src/jobs_status_manager/mail_service.py` | 邮件入库和事务外 LLM 分析 | 保留事务边界，接入真实 LLM |
| `src/jobs_status_manager/event_pipeline.py` | Outbox 发布和持久化重试 | 继续负责邮件分析事件的重试 |
| `src/jobs_status_manager/agent/contracts.py` | Conversation、Tool Call 应用层 schema | 保持 Provider-neutral |
| `src/jobs_status_manager/agent/turn_runtime.py` | 在线程中调用同步 `llm.converse()` | 保持同步 Adapter + `anyio.to_thread.run_sync` |
| `src/jobs_status_manager/infrastructure/adapters/protocols.py` | 外部能力 Protocol | 作为唯一 Protocol 定义位置 |
| `src/jobs_status_manager/infrastructure/adapters/factory.py` | 生产资源构造和关闭 | 增加 IMAP、LLM 及共享 HTTP client |
| `src/jobs_status_manager/application/lifecycle.py` | 生命周期、worker、恢复和关闭 | 接入 factory、readiness 和资源所有权 |
| `src/jobs_status_manager/infrastructure/adapters/fakes.py` | 确定性测试替身 | 保持 fake，不作为生产 fallback |

当前 `IMAPGateway.poll(account_key, cursor)` 返回 `list[MailEnvelope]`，不能表达空批次、首次基线或 `UIDVALIDITY` 变化。当前 mail poller 也把最后一封邮件的 `provider_message_id` 当作游标，这对 IMAP 不安全，必须修正。

当前 LLM Protocol 同时出现在 `mail.py` 和 `infrastructure/adapters/protocols.py`，必须合并为一份。当前应用层已经具备 `JobMailAnalysisInput`、`ConversationPrompt`、`ConversationResponse` 和 `ToolCallRequest`，不应把 OpenAI SDK 类型泄漏到应用层。

### 2.2 已通过与未通过

截至 2026-09-12，当前工作树确定性检查为：

```text
uv run pytest -q       267 passed
uv run ruff check .    PASS
uv run ruff format --check .  PASS
uv run basedpyright   0 errors, 0 warnings, 0 notes
uv lock --check       PASS
uv run alembic check  PASS
```

当前数据库 head 为 `0008_qq_reply_targets (head)`。这些结果证明本地已有能力，但不证明真实 QQ endpoint、真实 IMAP 或真实 LLM 兼容性。真实外部服务验收必须另设人工批准的 smoke window。

## 3. 不可改变的约束

后续实现不得引入以下变化：

- 不引入 Redis、Kafka、RabbitMQ、Celery、第二个服务进程或新的队列系统。
- 不改变单用户、单邮箱、单 QQ 账号、单进程约束。
- 不把 IMAP、LLM、QQ、Embedding、Chroma 或文件解析调用放进数据库事务。
- 不让 LLM 直接写入 Application、JobEvent、PendingAction 或其他业务事实。
- 所有写操作继续遵循 `Proposal -> PendingAction -> User Confirmation -> Execution`。
- 不在 Adapter 内增加独立的无限重试；持久化 worker/state machine 是重试唯一所有者。
- 不把 fake 作为生产配置缺失或 Provider 故障后的自动降级方案。
- 不宣称 exactly-once；系统语义是 at-least-once + 幂等消费者。
- 不做 OAuth、多邮箱账户、文件夹发现、IMAP IDLE、历史邮件回填或流式 LLM。

## 4. 目标数据流

### 4.1 邮件接收链路

```text
MailAccount.polling_cursor
        |
        v
IMAPGateway.poll(account_key, cursor)
        |
        | 事务外：连接、登录、SEARCH、FETCH、MIME 解析
        v
MailPollBatch(envelopes, next_cursor, reset)
        |
        v
逐封 ingest_mail()，依赖 UNIQUE(mail_account_id, provider_message_id) 幂等
        |
        v
全部入库成功后，在短事务中保存 next_cursor
        |
        v
MAIL_RECEIVED / JobMailAnalysis / Outbox / Notification
```

关键原则：如果批次中任何 envelope 入库失败，不能保存 `next_cursor`。下一次 poll 可以重新拉到已成功入库的邮件，唯一约束会吸收重复；不能因为一次部分失败而跳过未处理邮件。

### 4.2 LLM 链路

```text
mail_service.analyze_mail()
        |
        | 事务外
        v
LLMAdapter.analyze_job_mail(prompt)
        |
        v
OpenAICompatibleTransport -> POST /chat/completions
        |
        v
严格 JSON + Pydantic 校验 -> JobMailAnalysisInput
```

Conversation 链路同样使用同一个 LLM 实例，但消息构造、Tool schema、响应解析和错误分类必须与邮件分析分开实现。

## 5. 第一阶段：统一配置和 Protocol

### 5.1 配置目标

在 `settings.py` 增加 `APP_RUNTIME_MODE`、`APP_IMAP_*` 和 `APP_LLM_*`。推荐字段如下：

| 环境变量 | 规则 |
| --- | --- |
| `APP_RUNTIME_MODE` | 仅允许 `local`、`production`，默认 `local` |
| `APP_IMAP_ENABLED` | 默认 `false`；production 必须为 `true` |
| `APP_IMAP_HOST` | 默认 `imap.qq.com`，v1 精确校验 |
| `APP_IMAP_PORT` | 默认 `993`，v1 精确校验 |
| `APP_IMAP_SSL` | 默认 `true`，v1 必须为 `true` |
| `APP_IMAP_FOLDER` | 默认 `INBOX`，去空格后必须精确为 `INBOX` |
| `APP_IMAP_ACCOUNT` | 启用时必填，非空 |
| `APP_IMAP_AUTH_CODE` | QQ 邮箱授权码，使用 `SecretStr` |
| `APP_IMAP_CONNECT_TIMEOUT_SECONDS` | 默认 `10`，正整数 |
| `APP_IMAP_COMMAND_TIMEOUT_SECONDS` | 默认 `30`，正整数 |
| `APP_IMAP_BATCH_SIZE` | 默认 `100`，范围 `1..500` |
| `APP_LLM_ENABLED` | 默认 `false`；production 必须为 `true` |
| `APP_LLM_BASE_URL` | 启用时必填；显式 HTTPS 地址 |
| `APP_LLM_API_KEY` | 启用时必填，使用 `SecretStr` |
| `APP_LLM_MODEL` | 默认并固定为 `deepseek-flash` |
| `APP_LLM_CONNECT_TIMEOUT_SECONDS` | 默认 `5` |
| `APP_LLM_READ_TIMEOUT_SECONDS` | 默认 `60` |
| `APP_LLM_WRITE_TIMEOUT_SECONDS` | 默认 `15` |
| `APP_LLM_POOL_TIMEOUT_SECONDS` | 默认 `10` |
| `APP_LLM_MAX_CONNECTIONS` | 默认 `20` |
| `APP_LLM_MAX_KEEPALIVE_CONNECTIONS` | 默认 `10`，不能大于 max connections |

`APP_IMAP_PASSWORD` 不做兼容别名。删除 `imap_password`，改为 `imap_auth_code`；旧变量应因 `extra="forbid"` 被安全地报告为未知配置，但错误中不得出现旧值。

### 5.2 运行模式

- `local`：允许关闭 IMAP/LLM，允许显式注入 fake，用于 demo 和测试；health 必须显示 `local_only`，不能显示产品就绪。
- `production`：要求两个 enable gate 都为 `true`，配置完整，且必须构造真实 Adapter；缺配置时启动失败或 health 返回 503，不能静默跳过 worker。
- 显式传入 `LifecycleAdapters` 的对象优先于 factory，但生产入口不得传入 fake。

### 5.3 唯一 Protocol

`infrastructure/adapters/protocols.py` 是唯一外部 Protocol 定义位置。建议将 IMAP 契约修改为：

```python
class IMAPGateway(Protocol):
    def poll(self, account_key: str, cursor: str | None) -> MailPollBatch: ...
```

`MailPollBatch` 放在 `mail.py`，至少包含：

```text
envelopes: tuple[MailEnvelope, ...]
next_cursor: str
reset: bool
```

`LLMAdapter` 保留两个生产入口：

```python
def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput: ...
```

删除 `complete()` 前必须进行全仓引用检查。若没有真实调用方，删除协议、fake 中的 legacy 实现和对应测试；不要为未使用的自由文本接口保留第三条实现路径。

## 6. 第二阶段：实现 QQ IMAP Adapter

建议新增 `src/jobs_status_manager/infrastructure/adapters/qq_imap.py`，Adapter 保持同步接口，由现有 worker 在线程中调用。

### 6.1 单次 poll 的顺序

每次 `poll()` 都执行完整短连接：

1. 创建 SSL context，启用证书验证。
2. 连接 `imap.qq.com:993`。
3. 使用 `APP_IMAP_ACCOUNT` 和授权码登录。
4. 以只读方式选择 `INBOX`。
5. 读取当前 mailbox 的 `UIDVALIDITY` 和高水位信息。
6. 解析传入 cursor；首次、旧格式、非法 cursor 或 UIDVALIDITY 变化进入 reset 分支。
7. 对合法 cursor 使用 UID SEARCH，查询 `UID > persisted_uid` 的邮件。
8. 按 UID 升序截取 `APP_IMAP_BATCH_SIZE`。
9. 用 UID FETCH 获取完整 RFC 822 内容。
10. 将每封邮件转换为 `MailEnvelope`。
11. 计算本轮 `next_cursor`。
12. `logout()` 并关闭连接；异常时也必须释放 socket。

Adapter 不跨轮次复用连接。这样可以避免连接空闲失效、状态漂移和关闭归属不清。

### 6.2 Cursor 设计

cursor 与 `provider_message_id` 是两个不同概念：

- cursor 用于定位 IMAP 高水位。
- `provider_message_id` 用于数据库幂等。

建议 cursor 使用版本化不透明格式，例如 `imap-v1.<base64url-canonical-json>`，内部包含：

```json
{"version":1,"uidvalidity":123456,"uid":789}
```

最终 UTF-8 长度必须不超过现有 `mail_accounts.polling_cursor` 的 255 字符限制。任何编码、解码、长度和字段范围都必须有单元测试。

规则：

- 首次 poll 不导入历史邮件，只读取当前高水位并返回 `reset=true` 的空批次。
- cursor 缺失、非法、旧版本或以前保存的是邮件 ID 时，按 reset 处理。
- UIDVALIDITY 改变时建立新基线，不把旧 UID 与新 UID 关联。
- UID 回退或响应不满足单调性时，不继续猜测；安全地 reset 或返回可分类错误，具体选择必须由测试固定。
- 普通成功批次的 `next_cursor` 使用 Adapter 计算值，poller 不从最后一封邮件自行推导。
- 空批次也可能推进 cursor，例如 mailbox 高水位变化但没有可取邮件。

`MailEnvelope.provider_message_id` 建议使用：

```text
qq-imap-v1:<uidvalidity>:<uid>
```

不得使用可能缺失或重复的 RFC `Message-ID` 作为唯一幂等键。

### 6.3 MIME 解析

使用标准库 `email` parser 和现代 policy。必须覆盖：

- RFC 2047 中文主题解码。
- 发件人、收件人和抄送地址的规范化。
- 合并 `To`/`Cc`，排除 `Bcc`，保持顺序并去重。
- 去除地址和字段中的 CR/LF，防止日志或后续文本注入。
- 优先选择 `text/plain`；只有没有纯文本时才对 HTML 做有界的文本降级。
- 不把附件内容拼入正文；正文大小必须有上限。
- 统一换行符，移除 NUL 和不需要的控制字符。
- 优先使用邮件 `Date` 转 UTC，缺失或非法时使用 IMAP INTERNALDATE。
- 非法字节、损坏 multipart、空正文和超限正文都必须有确定行为。

解析失败不能记录原始邮件。单封坏邮件是否跳过并记录失败，还是让批次失败并保持 cursor，必须以测试和产品决策为准；默认优先保证不丢邮件，因此不得在未持久化失败状态的情况下直接跳过。

### 6.4 IMAP 错误分类

Adapter 只抛出安全、可分类的错误，建议至少覆盖：

```text
configuration_error
authentication_failed
tls_failed
connection_failed
timeout
mailbox_not_found
protocol_error
message_parse_failed
temporary_provider_error
permanent_provider_error
```

错误对象只允许包含错误类别、HTTP/IMAP 状态（如有）、是否可重试、内部 request kind 和短 provider code。不得包含授权码、完整服务器响应、完整 cursor 或邮件正文。Adapter 不自行重试。

## 7. 第三阶段：实现 OpenAI-compatible LLM Adapter

建议拆成两个层次：

1. `OpenAICompatibleTransport`：HTTP client、认证、超时、请求发送和安全错误分类。
2. `OpenAICompatibleLLM` 或 `DeepSeekLLMAdapter`：邮件分析、Conversation、Tool schema 和应用层响应校验。

### 7.1 HTTP Transport

使用现有 `httpx2` 依赖模式，创建一个可注入的同步 client。生产 lifespan 内只创建一个 Adapter 和一个 client；测试可注入 client，Adapter 不得关闭外部拥有的 client。

请求要求：

- 请求 `POST <normalized-base-url>/chat/completions`。
- `Authorization: Bearer <api-key>`。
- `Content-Type: application/json`。
- `stream=false`，v1 不实现 streaming。
- transport retry 设置为 `0`；不设置 redirect 跟随。
- base URL 的尾斜杠、`/v1` 和自定义路径前缀由 URL 单元测试固定，禁止简单字符串拼接造成重复或丢失路径。

响应错误映射：

| 情况 | 分类 | 是否进入持久化重试 |
| --- | --- | --- |
| 配置不完整 | `configuration_error` | 否，启动前修复 |
| 401/403 | `authentication_failed` | 否或有限人工干预 |
| 其他确定性 4xx | `request_rejected` | 否 |
| 429 | `rate_limited` | 是，遵循 durable retry |
| 5xx | `provider_failure` | 是，遵循 durable retry |
| 超时/连接错误 | `transport_failure` | 是，遵循 durable retry |
| JSON 或应用 schema 错误 | `contract_violation` | 按业务状态机固定，不发 repair 请求 |

### 7.2 邮件分析

`analyze_job_mail()` 必须：

1. 只接受已有 `bounded_prompt()` 产生的有界 prompt。
2. 构造固定 system instruction 和用户消息。
3. 请求非流式 JSON object。
4. 要求恰好一个 choice，且内容非空、无 tool call。
5. 解码 JSON 并调用 `JobMailAnalysisInput.model_validate()`。
6. 拒绝缺字段、错误类型、超长结果、额外无法解释的结果和空内容。
7. `AnalysisMetadata.model_name` 使用实际配置的 `deepseek-flash`。

不要用第二次 LLM 请求修复第一次的非法 JSON。修复型请求会放大成本、延长状态窗口，并把不可信输出变成不可控递归。

### 7.3 Conversation 和 Tool Call

将 `ConversationPrompt` 映射为标准 messages，顺序必须稳定：system、bounded context、近期消息、当前用户消息和工具结果。工具调用的 ID 必须在 assistant tool call 与后续 tool message 间保持一致。

Tool schema 从 `agent.tools.definitions()` 生成，不能在 LLM Adapter 里复制一份工具注册表。必须：

- 只暴露当前 registry 中的工具。
- 只接受 function tool。
- 只接受一个最终回答或一个 tool call。
- 拒绝 multiple choices、answer + tool、multiple tool calls、未知工具、非法 JSON arguments。
- 保留原有 `PendingAction` 确认门，LLM 不能绕过确认直接写入。

最终返回必须是现有的 `ConversationResponse`；应用层不能接收 OpenAI response dict。

## 8. 第四阶段：接入 Mail Pipeline

修改 `mail_poller.poll_once()` 时保持以下顺序：

1. 在短事务中读取 `MailAccount` 和旧 cursor。
2. 提交并退出事务。
3. 在事务外调用 `IMAPGateway.poll()`。
4. 对 `batch.envelopes` 逐封调用现有入库服务。
5. 所有 envelope 成功处理后，在新事务中保存 `batch.next_cursor`、poll 时间和统计信息。
6. 任一步骤失败时保留旧 cursor，并记录可安全观测的失败状态。

不要在一个长事务中包住 IMAP 或 LLM 网络调用。不要把 cursor 更新放在单封邮件成功之后，否则批次中后续邮件失败时会产生跳过风险。

现有不变量必须继续成立：

- `UNIQUE(mail_account_id, provider_message_id)` 吸收重复邮件。
- 每封 Mail 至多一个 `JobMailAnalysis`。
- 同一封邮件不重复创建首个 `MAIL_RECEIVED` 分析事件。
- Outbox 仍为 at-least-once，`ProcessedEvent` 负责消费幂等。
- `shouldUpdate=true` 时不发送普通邮件通知，而由 PendingAction 确认通知承担提示。

LLM 错误必须交给现有邮件分析状态机分类。Adapter 的错误分类不能和 Outbox、AgentRun 的重试逻辑叠加成多个独立 retry loop。

## 9. 第五阶段：Factory、Lifecycle 和 Readiness

### 9.1 资源所有权

扩展 `OwnedAdapters` 或等价资源容器，使其明确拥有：

- IMAP Adapter。
- LLM Adapter 和其 HTTP client。
- 已有 Embedding Adapter。
- Chroma Adapter。
- QQ botpy client 和 transport。

构造顺序应稳定，建议为：配置校验 -> 数据库 -> LLM -> IMAP -> Embedding/Chroma -> QQ -> worker。后续构造失败时，按逆序关闭已创建资源，且不能掩盖根异常。

注入优先级固定为：显式 `LifecycleAdapters` 值 > factory 创建的 production Adapter > `None`。同一个 lifespan 内 mail worker 和 Agent worker 必须拿到同一个 LLM 对象。

关闭顺序：停止/取消 worker -> 关闭 QQ runtime 和 transport -> 关闭拥有的 Adapter/client -> dispose database。关闭方法必须幂等；注入的外部 client 不得被 Adapter 关闭。

### 9.2 Health 语义

`GET /health` 不得为探测状态而登录 IMAP 或调用 LLM。它只报告已知状态，例如：

```text
runtime_mode
database
schema
imap: disabled | configured | unavailable | failed
llm: disabled | configured | unavailable | failed
product_readiness: local_only | ready | not_ready
```

`local` 且能力门关闭时，返回 `local_only`。`production` 缺少真实 IMAP/LLM 或构造失败时返回 503；不得因 worker 被静默跳过而返回 200 ready。瞬时运行失败和永久认证失败的 readiness 降级规则必须在实现和测试中明确，不得由日志文本推断。

## 10. 测试实施顺序

### 10.1 Unit tests

新增或扩展：

- `tests/unit/test_settings.py`：前缀、默认值、旧密码字段拒绝、生产门控、URL 和范围校验。
- `tests/unit/test_qq_imap_cursor.py`：编码、解码、长度、UIDVALIDITY、非法和 reset。
- `tests/unit/test_qq_imap_mime.py`：中文主题、multipart、HTML、附件、日期、坏字节、超限正文。
- `tests/unit/test_openai_compatible_llm.py`：请求 payload、URL 规范化、response schema、工具调用拒绝条件和安全错误。
- `tests/unit/test_factory.py`：共享实例、注入优先级、client close ownership、部分构造失败清理。

### 10.2 Integration tests

使用真实临时 SQLite、真实 migration 和现有 fake：

- poller 使用 `MailPollBatch` 推进 cursor。
- 空批次和 reset 批次可安全保存 cursor。
- 批次部分失败时 cursor 不变。
- 重复 fetch 不创建重复 Mail、JobMailAnalysis 或首个事件。
- LLM 5xx、429、超时进入正确 durable state。
- 外部调用不在数据库事务内。
- worker 重启后能恢复 cursor、Outbox、AgentRun 和 PendingAction。

### 10.3 HTTP contract tests

使用可控本地 HTTP server 或注入 transport，不访问真实 Provider。至少验证：

- Authorization header 存在但不会进入异常、日志或断言输出。
- 非 2xx 的安全分类。
- malformed JSON、空 choices、多个 choices、tool/answer 混合均被拒绝。
- 429/5xx 不由 Adapter 内部重复请求。
- tool arguments 仅进入现有 registry 和 PendingAction 流程。

### 10.4 IMAP contract tests

优先使用可控本地 IMAP 服务或协议级 socket fake。必须能故障注入：连接断开、登录失败、select 失败、UIDVALIDITY 改变、FETCH 中断、单封 MIME 损坏、空 INBOX 和 UID 非单调。

## 11. 推荐提交顺序

每个提交只完成一类变化，避免把配置、IMAP、LLM、生命周期和部署混成一个无法回滚的提交：

1. `test(settings): define IMAP and LLM configuration gates`
2. `feat(settings): add production IMAP and LLM settings`
3. `test(protocols): define mail poll batch and adapter contracts`
4. `refactor(protocols): consolidate external adapter protocols`
5. `test(imap): add cursor and MIME contract tests`
6. `feat(imap): implement QQ cursor and MIME boundaries`
7. `test(imap): add local adapter protocol tests`
8. `feat(imap): implement QQ IMAP polling adapter`
9. `test(mail): verify batch cursor advancement and idempotency`
10. `feat(mail): integrate explicit IMAP next cursor`
11. `test(llm): define OpenAI-compatible request and response contracts`
12. `feat(llm): implement shared OpenAI-compatible LLM adapter`
13. `test(lifecycle): verify factory ownership and readiness`
14. `feat(lifecycle): wire shared IMAP and LLM adapters`
15. `test(e2e): add credential-free IMAP and LLM pipeline paths`
16. `docs(operations): document deployment, smoke, rollback, and acceptance`
17. `docs(implementation): finalize Chinese implementation guide`

除非实现证明现有 255 字符 cursor 字段不足，否则不要创建 Alembic migration。不要为了表示这项工作而创建空 migration。

## 12. 部署和人工 Smoke

### 12.1 本地检查

```bash
uv sync
cp .env.example .env
chmod 600 .env
uv run python -m jobs_status_manager migrate
uv run python -m jobs_status_manager bootstrap
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic check
```

### 12.2 生产配置

生产 `.env` 至少应提供 IMAP 和 LLM 两组完整配置，以及现有数据库、bootstrap、QQ 和目录权限配置。不得把邮箱授权码、LLM key、QQ secret 或备份文件提交到 Git。`APP_LLM_BASE_URL` 必须由操作者验证其 HTTPS、路径前缀和 Chat Completions 兼容性。

仍保持一个进程、一个数据库、一个 listener。Starlette 是唯一 listener，不能另起 botpy HTTP server。

### 12.3 人工批准的 live smoke

真实验证不属于普通 CI。开始前必须人工批准：测试邮箱、测试 QQ 用户、非敏感测试邮件、Provider endpoint、凭据注入方式、预期副作用、停止条件和清理负责人。

按以下顺序执行：

1. `migrate`、`bootstrap`，确认 schema 和目录权限。
2. 启动应用，确认 production readiness；输出不得包含 secret。
3. 验证 IMAP TLS、登录和只读 `INBOX`。
4. 首次 poll 只建立 baseline，不导入历史邮件。
5. 发送一封非敏感测试邮件，确认只产生一个 Mail 和一个分析流程。
6. 重启一次，确认不重复创建 Mail、JobMailAnalysis、Outbox 或业务事实。
7. 验证真实 `deepseek-flash` 结构化分析。
8. 验证一次只读 Conversation 和一个只读 tool call。
9. 验证写建议只创建 PendingAction；确认后才产生一次业务写入。
10. 注入一次受控的 5xx 或 timeout，确认 durable retry，不产生重复业务事实。
11. 停止进程，确认连接、worker、QQ runtime 和数据库按顺序关闭。
12. 清理测试邮件、上传文件、日志和临时数据库，确认无 `.part` 文件和残留凭据。

每一步只记录 `PASS`、`FAIL`、`BLOCKED` 或 `NOT_RUN`、时间、版本、endpoint hostname、状态码、脱敏 trace ID、观察到的副作用和清理结果。禁止记录完整邮箱地址、message content、prompt、response、Authorization header、URL query 或附件内容。

立即停止并将剩余步骤标为 `BLOCKED` 的情况包括：证书错误、endpoint 不一致、凭据泄漏、意外历史导入、意外收件人、重复业务写入、未清理文件或 ambiguous provider outcome。

## 13. 验收门

发布必须按以下门逐一通过：

| 门 | 通过条件 |
| --- | --- |
| G1 配置 | 新变量、秘密、production/local 门控和旧字段拒绝均有测试 |
| G2 契约 | 唯一 Protocol、MailPollBatch、LLM response contract 已固定 |
| G3 持久化 | cursor、幂等、Outbox、重启恢复和部分失败行为通过 |
| G4 生命周期 | 共享实例、关闭顺序、readiness、无 fake fallback 通过 |
| G5 本地 E2E | 本地 IMAP + 本地 OpenAI-compatible server + 临时 SQLite 全链路通过 |
| G6 真实 Smoke | 人工批准，IMAP、LLM、QQ 必要能力逐项有证据 |
| G7 观察期 | 无未解释 retry、重复事实、cursor 跳跃、secret 泄漏或资源泄漏 |

只有 G1-G5 通过才能合并确定性实现；G6-G7 未通过时必须保持 `BLOCKED`，不能用 fake 或单元测试结果替代真实生产兼容性。

## 14. 未决决策清单

以下事项在编码前必须由实现负责人明确并写入测试：

1. `complete()` 是否经全仓引用检查后删除。
2. `MailPollBatch` 的最终模块位置和字段是否增加 `reset_reason`。
3. base URL 包含 `/v1` 或自定义路径时的精确拼接规则。
4. 非法单封邮件是批次失败，还是写入可恢复的 ingestion failure 记录后继续。
5. `UIDVALIDITY` 改变时 reset 的可观测字段和运维告警方式。
6. LLM schema 错误的最终状态是有限 durable retry 还是 terminal failure。
7. transient IMAP/LLM failure 对 `/health` 的降级阈值。
8. production 配置错误在 Settings 构造期、lifespan 启动期或两处都校验。
9. 是否需要为 cursor 增加 migration；只有 255 字符不足时才允许增加。
10. live smoke 的测试账户、凭据管理、清理负责人和停止条件。

未决项不能靠兼容别名、隐式 fallback 或宽松字典解析“暂时解决”。

## 15. 完成定义

后续工作只有同时满足以下条件才算完成：

- IMAP 和 LLM Adapter 均是真实实现，且通过无凭据 contract tests。
- 所有外部调用都在事务外，cursor 仅在批次完整持久化后推进。
- 邮件 ID 与 IMAP cursor 分离，重复 fetch 不重复业务事实。
- 邮件分析和 Conversation 共用一个 LLM 实例，但使用独立的严格响应解析。
- Tool Call 仍受 registry、权限和 PendingAction 约束。
- production 不会静默跳过 IMAP/LLM，也不会自动降级为 fake。
- 资源所有权、关闭顺序和 health readiness 有测试覆盖。
- `pytest`、Ruff、format、basedpyright、lock 和 Alembic 检查通过。
- 真实 Provider 验收状态被准确标为 `PASS` 或 `BLOCKED`，没有以本地 fake 结果冒充生产兼容性。
