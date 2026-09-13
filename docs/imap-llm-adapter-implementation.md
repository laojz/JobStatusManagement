# QQ 邮箱 IMAP 与 DeepSeek LLM 适配器实现说明

## 1. 文档目的

本文定义生产 QQ 邮箱 IMAP 适配器和 OpenAI 兼容 DeepSeek LLM 适配器的实现契约。后续编码代理应按本文完成代码、测试、配置更新和部署接线，不再重新选择协议、配置名、游标模型、重试层或生命周期方案。

本文只规划实现，不包含 Python 代码、数据库迁移或真实凭据。所有示例值都是占位符。

## 2. 范围

本轮必须交付两项生产能力：

1. 使用 QQ 邮箱 `imap.qq.com:993`、SSL、`INBOX` 和邮箱授权码轮询一个邮箱账户。
2. 使用 OpenAI 兼容 HTTP 协议调用一个显式配置的 LLM 服务，固定模型为 `deepseek-flash`，同时服务邮件分析和 Conversation Agent。

实现必须保持当前单用户、单邮箱、单 QQ 账号、单进程、AnyIO、Starlette、SQLite WAL 和数据库驱动 worker 架构。不得引入第二个服务进程或新的任务系统。

## 3. 当前仓库基线

### 3.1 已有能力

当前仓库已经具备以下骨架：

| 位置 | 当前职责 | 本轮关系 |
| --- | --- | --- |
| `src/jobs_status_manager/config/settings.py` | `APP_` 前缀、`.env` 加载、未知字段拒绝、`SecretStr` | 增加规范化 IMAP/LLM 配置，替换含糊的 `imap_password` |
| `src/jobs_status_manager/infrastructure/adapters/protocols.py` | 外部适配器协议集合 | 作为 `IMAPGateway` 和 `LLMAdapter` 的唯一规范定义位置 |
| `src/jobs_status_manager/mail.py` | `MailEnvelope`、邮件分类、`JobMailAnalysisInput`、提示构造 | 保留邮件数据结构，删除这里重复的协议声明 |
| `src/jobs_status_manager/mail_poller.py` | 事务外轮询、逐封幂等入库、成功后推进游标 | 改为消费适配器返回的版本化不透明游标 |
| `src/jobs_status_manager/mail_service.py` | 规则分类、事务外 LLM 分析、结构化结果持久化 | 注入真实 LLM，保留现有事务边界 |
| `src/jobs_status_manager/event_pipeline.py` | at-least-once Outbox 发布和持久化重试 | 继续拥有邮件分析事件的重试，不把重试下沉到 LLM 适配器 |
| `src/jobs_status_manager/agent/contracts.py` | `ConversationPrompt`、`ConversationResponse`、`ToolCallRequest` | 作为 Conversation 的应用层输入输出契约 |
| `src/jobs_status_manager/agent/turn_runtime.py` | 通过 `anyio.to_thread.run_sync` 调用同步 LLM | 保持同步适配器和线程调用方式 |
| `src/jobs_status_manager/application/lifecycle.py` | `LifecycleAdapters`、worker、启动恢复、关闭顺序 | 构造并注入一个共享 LLM 实例和生产 IMAP 实例 |
| `src/jobs_status_manager/infrastructure/adapters/factory.py` | 生命周期资源构造和关闭 | 扩展为统一拥有 IMAP、LLM、Embedding、Chroma 资源 |
| `src/jobs_status_manager/infrastructure/adapters/fakes.py` | 确定性测试替身 | 只用于测试和显式 demo，不作为生产失败后的自动 fallback |

当前 `poll_once()` 已经先读取账户和游标，关闭事务，再调用 `gateway.poll()`，随后逐封执行幂等入库，最后更新账户轮询状态。这一总体顺序正确。当前缺口是游标被错误地等同于最后一封邮件的 `provider_message_id`，无法表达 IMAP 的 `UIDVALIDITY + UID`。

当前 `analyze_mail()` 已经在数据库事务外调用 `llm.analyze_job_mail()`。`agent/turn_runtime.py` 也已在 AnyIO worker thread 中调用同步 `llm.converse()`。这两个边界必须保留。

### 3.2 已有可靠性约束

仓库现有测试和运维文档已经固定以下原则：

1. `Mail` 以 `(mail_account_id, provider_message_id)` 幂等。
2. `JobMailAnalysis` 与 `Mail` 一对一，重复分析更新当前结果，不重复创建首个分析事件。
3. Outbox 使用 at-least-once 发布和 `ProcessedEvent` 幂等消费，不宣称 exactly-once。
4. LLM 只能产生分析结果、答案或 Tool Call，不能直接修改真实业务事实。
5. 所有真实 Application 和知识库写入都必须先创建 `PendingAction`，用户确认后才执行冻结参数。
6. 所有外部调用必须发生在数据库事务之外。
7. `GET /health` 当前只证明数据库和 schema 可用，还不能证明 IMAP 或 LLM 已配置、已构造或满足生产产品能力。

## 4. 最终决策

以下决策不可在实现阶段自行改动：

| 主题 | 决策 |
| --- | --- |
| 配置前缀 | 全部使用 `APP_` |
| 邮箱服务 | QQ 邮箱 |
| IMAP 地址 | `imap.qq.com` |
| IMAP 端口 | `993` |
| 传输安全 | SSL，从连接开始即加密，不使用明文升级 |
| 文件夹 | `APP_IMAP_FOLDER=INBOX`，v1 必须精确校验为 `INBOX` |
| 登录账户 | `APP_IMAP_ACCOUNT` |
| 登录秘密 | `APP_IMAP_AUTH_CODE`，含义是 QQ 邮箱授权码 |
| 账户数量 | 一个 |
| OAuth | 不支持 |
| 多文件夹 | 不支持 |
| IMAP IDLE | 不支持，只做定时轮询 |
| IMAP 连接生命周期 | 每次 poll 新建一个 SSL 连接，完成后退出登录并关闭 |
| IMAP 游标 | 版本化不透明 `UIDVALIDITY + UID`，保存在现有 `mail_accounts.polling_cursor` |
| LLM 协议 | OpenAI 兼容 Chat Completions 线协议 |
| LLM base URL | 必须由操作者显式配置，不提供或猜测供应商默认地址 |
| LLM 模型 | `deepseek-flash` |
| LLM 配置 | 邮件分析与 Conversation 共用一套配置和模型 |
| LLM 实例 | 应用 lifespan 内只构造一个适配器实例 |
| LLM HTTP 客户端 | 一个可复用的同步 HTTP client，由适配器持有并在 shutdown 关闭 |
| 异步模型 | 适配器保持同步，通过现有 AnyIO worker thread 调用 |
| Fake | 仅测试和显式 demo 使用，生产不能自动降级到 fake |

## 5. 规范配置

### 5.1 运行模式和启用门控

新增 `APP_RUNTIME_MODE`，允许值只有 `local` 和 `production`，默认 `local`。该字段用于区分本地无凭据开发与生产产品就绪，不能用是否恰好存在某个 secret 来猜运行意图。

新增 `APP_IMAP_ENABLED` 和 `APP_LLM_ENABLED`，默认均为 `false`。

规则如下：

1. `local` 模式允许两个门控关闭，应用仍可运行 fake demo，但 health 必须明确标记 `local_only`，不能显示完整产品就绪。
2. `production` 模式要求 `APP_IMAP_ENABLED=true` 和 `APP_LLM_ENABLED=true`，并要求各自配置完整。缺失时配置加载或启动失败，`GET /health` 不得返回产品 ready。
3. 门控开启但配置不完整时必须安全失败，不得构造 fake 替代品。
4. 通过 `LifecycleAdapters` 显式注入的测试适配器优先于工厂构造，但只在测试或显式 CLI demo 中使用。生产启动路径不得注入 fake。

### 5.2 IMAP 配置表

| 环境变量 | Settings 字段 | 类型 | 默认值 | 规则 |
| --- | --- | --- | --- | --- |
| `APP_IMAP_ENABLED` | `imap_enabled` | `bool` | `false` | 生产必须为 `true` |
| `APP_IMAP_HOST` | `imap_host` | `str` | `imap.qq.com` | v1 精确校验为 `imap.qq.com` |
| `APP_IMAP_PORT` | `imap_port` | `int` | `993` | v1 精确校验为 `993` |
| `APP_IMAP_SSL` | `imap_ssl` | `bool` | `true` | v1 必须为 `true` |
| `APP_IMAP_FOLDER` | `imap_folder` | `str` | `INBOX` | 去除首尾空格后必须精确等于 `INBOX` |
| `APP_IMAP_ACCOUNT` | `imap_account` | `str \| None` | 无 | 启用时必填，去除首尾空格后非空 |
| `APP_IMAP_AUTH_CODE` | `imap_auth_code` | `SecretStr \| None` | 无 | 启用时必填，空字符串按缺失处理 |
| `APP_IMAP_CONNECT_TIMEOUT_SECONDS` | `imap_connect_timeout_seconds` | `int` | `10` | 正整数 |
| `APP_IMAP_COMMAND_TIMEOUT_SECONDS` | `imap_command_timeout_seconds` | `int` | `30` | 正整数，覆盖登录、select、search、fetch |
| `APP_IMAP_BATCH_SIZE` | `imap_batch_size` | `int` | `100` | `1..500`，限制每轮处理量 |

`APP_IMAP_ACCOUNT` 是登录邮箱账号，也是用户批准的单邮箱配置名。数据库中的 `bootstrap_mail_account_key` 仍是内部邮箱身份键，两者不能混用。适配器 `poll(account_key, cursor)` 接收的 `account_key` 用于核对当前数据库账户身份和日志关联，不作为网络登录名。网络登录始终使用 `APP_IMAP_ACCOUNT`。

### 5.3 废弃 `APP_IMAP_PASSWORD`

当前 `settings.py` 和 `.env.example` 使用含糊的 `APP_IMAP_PASSWORD`。实现必须在发布前完成以下替换：

1. 删除 `imap_password` 字段。
2. 新增 `imap_auth_code: SecretStr | None`。
3. 将示例和测试统一改为 `APP_IMAP_AUTH_CODE`。
4. 不得长期同时支持两个 secret 名，也不得设置静默优先级。
5. 如果加载环境时发现旧的 `APP_IMAP_PASSWORD`，由于 settings 已启用 `extra="forbid"`，应返回安全的未知配置错误，提示操作者改名，但不显示旧值。

这是发布前配置更名，不是需要兼容的已发布外部协议。双字段兼容只会制造秘密来源歧义。

### 5.4 LLM 配置表

| 环境变量 | Settings 字段 | 类型 | 默认值 | 规则 |
| --- | --- | --- | --- | --- |
| `APP_LLM_ENABLED` | `llm_enabled` | `bool` | `false` | 生产必须为 `true` |
| `APP_LLM_BASE_URL` | `llm_base_url` | `AnyHttpUrl \| None` | 无 | 启用时必填，必须为 HTTPS，不猜供应商地址 |
| `APP_LLM_API_KEY` | `llm_api_key` | `SecretStr \| None` | 无 | 启用时必填，空字符串按缺失处理 |
| `APP_LLM_MODEL` | `llm_model` | `str` | `deepseek-flash` | v1 精确校验为 `deepseek-flash` |
| `APP_LLM_CONNECT_TIMEOUT_SECONDS` | `llm_connect_timeout_seconds` | `int` | `5` | 正整数 |
| `APP_LLM_READ_TIMEOUT_SECONDS` | `llm_read_timeout_seconds` | `int` | `60` | 正整数 |
| `APP_LLM_WRITE_TIMEOUT_SECONDS` | `llm_write_timeout_seconds` | `int` | `15` | 正整数 |
| `APP_LLM_POOL_TIMEOUT_SECONDS` | `llm_pool_timeout_seconds` | `int` | `10` | 正整数 |
| `APP_LLM_MAX_CONNECTIONS` | `llm_max_connections` | `int` | `20` | 正整数 |
| `APP_LLM_MAX_KEEPALIVE_CONNECTIONS` | `llm_max_keepalive_connections` | `int` | `10` | `1..max_connections` |

不要在本文或实现中写死未经核验的 DeepSeek endpoint、token 上限或供应商扩展字段。`APP_LLM_BASE_URL` 是 OpenAI 兼容 API 根地址，适配器在其下请求标准 Chat Completions 路径。该路径与操作者提供的 base URL 如何拼接必须由 URL 规范化单元测试固定，禁止字符串随意拼接导致重复 `/v1` 或丢失路径前缀。

### 5.5 无凭据示例

以下示例只展示字段形状，不包含真实邮箱、地址或 secret：

```dotenv
APP_RUNTIME_MODE=production

APP_IMAP_ENABLED=true
APP_IMAP_HOST=imap.qq.com
APP_IMAP_PORT=993
APP_IMAP_SSL=true
APP_IMAP_FOLDER=INBOX
APP_IMAP_ACCOUNT=<provided-by-operator>
APP_IMAP_AUTH_CODE=<provided-by-operator>
APP_IMAP_CONNECT_TIMEOUT_SECONDS=10
APP_IMAP_COMMAND_TIMEOUT_SECONDS=30
APP_IMAP_BATCH_SIZE=100

APP_LLM_ENABLED=true
APP_LLM_BASE_URL=<operator-provided-openai-compatible-base-url>
APP_LLM_API_KEY=<provided-by-operator>
APP_LLM_MODEL=deepseek-flash
APP_LLM_CONNECT_TIMEOUT_SECONDS=5
APP_LLM_READ_TIMEOUT_SECONDS=60
APP_LLM_WRITE_TIMEOUT_SECONDS=15
APP_LLM_POOL_TIMEOUT_SECONDS=10
APP_LLM_MAX_CONNECTIONS=20
APP_LLM_MAX_KEEPALIVE_CONNECTIONS=10
```

`.env` 必须保持 `chmod 600`。日志、异常、health、测试输出和验收证据均不得包含邮箱账号、授权码、API key、Authorization header、完整请求体或完整响应体。

## 6. 规范协议和数据结构

### 6.1 唯一协议定义位置

`infrastructure/adapters/protocols.py` 是所有外部能力协议的唯一规范位置。

实现时必须：

1. 保留并完善该文件中的 `IMAPGateway` 和 `LLMAdapter`。
2. 从 `mail.py` 删除重复的 `IMAPGateway` 和 `LLMAdapter` 声明及不再需要的 `Protocol` import。
3. 所有调用方统一从 `infrastructure.adapters.protocols` 导入协议。
4. `mail.py` 继续保存 provider-neutral 邮件数据 schema，例如 `MailEnvelope`、`JobMailAnalysisInput`，以及新增的 IMAP poll 结果数据对象。
5. `agent/contracts.py` 继续保存 Conversation 和 Tool Call schema，不把 OpenAI SDK 或 HTTP payload 类型泄露到应用层。

### 6.2 IMAP 协议

当前 `poll() -> list[MailEnvelope]` 无法在空批次、初始基线和 UIDVALIDITY 变化时推进游标。规范协议应改为等价于：

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

语义：

1. `envelopes` 按 UID 严格升序。
2. `next_cursor` 是本轮安全高水位，不由 poller 从最后一封邮件推导。
3. `reset=true` 表示适配器因首次启动、旧格式游标或 UIDVALIDITY 变化建立了新基线，本轮不会返回历史邮件。
4. 成功返回时 `next_cursor` 必须非空且长度不超过 255。
5. poller 只有在所有返回 envelope 均完成幂等入库后，才保存 `next_cursor`。
6. 任一入库失败时不得推进游标。下一轮可能再次返回已成功入库的 envelope，由唯一约束吸收重复。

`MailEnvelope.provider_message_id` 对 QQ IMAP 固定使用版本化稳定值，例如：

```text
qq-imap-v1:<uidvalidity>:<uid>
```

它用于邮件幂等，不等同于轮询游标，也不使用可能缺失或重复的 RFC `Message-ID`。

### 6.3 LLM 协议

规范 `LLMAdapter` 保留两个生产入口：

```python
class LLMAdapter(Protocol):
    def analyze_job_mail(self, prompt: str) -> JobMailAnalysisInput: ...
    def converse(self, prompt: ConversationPrompt) -> ConversationResponse: ...
```

当前 `complete(prompt: str) -> str` 没有实际生产调用方，只存在于协议和 fake。实现前先用引用检查确认仍无调用，然后删除该 legacy 方法。不要为未使用的自由文本 completion 保留第三条线协议。

同一个 `OpenAICompatibleLLM` 实例实现邮件分析和 Conversation。两者共享 base URL、API key、model 和 HTTP client，但使用不同的请求构造与响应解析函数，不能把两种响应 schema 混成一个宽松字典。

## 7. QQ 邮箱 IMAP 适配器

### 7.1 建议文件和职责

新增 `src/jobs_status_manager/infrastructure/adapters/qq_imap.py`，只负责：

1. 建立和关闭 QQ IMAP SSL 连接。
2. 登录、选择 `INBOX`、读取 UIDVALIDITY。
3. 解析和生成版本化游标。
4. 按 UID 搜索和批量抓取邮件。
5. 将 RFC 5322 和 MIME 转为 `MailEnvelope`。
6. 将库异常映射为安全类型化错误。

它不访问数据库，不决定轮询间隔，不创建 `Mail`，不调用 LLM，也不拥有持久化重试。

### 7.2 每轮连接流程

每次 `poll()` 必须使用一个全新的 SSL 连接：

```text
创建 SSL context
  ↓
连接 imap.qq.com:993，受 connect timeout 限制
  ↓
使用 APP_IMAP_ACCOUNT + APP_IMAP_AUTH_CODE 登录
  ↓
SELECT INBOX，只读模式
  ↓
读取 UIDVALIDITY 和 UIDNEXT 或当前最大 UID
  ↓
解析游标并决定初始、续读或 reset
  ↓
UID SEARCH + UID FETCH，最多 APP_IMAP_BATCH_SIZE
  ↓
按 UID 升序转换 MIME
  ↓
LOGOUT
  ↓
返回 MailPollBatch
```

要求：

1. 使用系统信任根创建默认 SSL context，启用主机名和证书校验。
2. 不允许关闭证书验证，不允许回退到明文，不允许接受自签名证书配置。
3. 使用 UID 命令，不使用 sequence number 作为持久化身份。
4. `SELECT` 必须是只读，适配器不得修改 Seen、Deleted、Flagged 等邮箱状态。
5. `LOGOUT` 失败不能覆盖已经产生的主要异常。连接对象仍需关闭。
6. 每轮新建连接，避免跨轮使用失效 socket、服务器超时或线程并发共享连接。

### 7.3 游标编码

游标是应用私有、不透明、版本化的 ASCII 字符串。规范逻辑内容为：

```json
{"v":1,"uidvalidity":123456,"uid":7890}
```

规范外层编码使用无填充 base64url，并加固定前缀：

```text
imap-v1.<base64url-canonical-json>
```

JSON 键顺序固定，使用紧凑分隔符，整数必须为非负十进制值。编码函数必须断言最终 UTF-8 长度不超过现有 `polling_cursor VARCHAR(255)`。当前字段足够，不需要为 v1 扩列。

`uid` 表示已经完成持久化的最高 UID。搜索范围是 `uid + 1:*`，结果再按升序截取 `APP_IMAP_BATCH_SIZE`。

### 7.4 首次启动和 reset

首次启动和 reset 采用基线策略，不导入历史存量邮件：

1. `cursor is None` 时，读取当前 UIDVALIDITY 和当前最大 UID。
2. 返回空 `envelopes`、指向当前最大 UID 的 `next_cursor`、`reset=true`。
3. 当前文件夹为空时，基线 UID 为 `0`。
4. 下一轮只读取基线之后到达的邮件。

以下情况也执行相同 reset：

1. 游标不是 `imap-v1.` 格式，包括当前 fake 或早期实现保存的 provider message ID。
2. base64url 或 JSON 无法解析。
3. schema version 不支持。
4. 游标 UIDVALIDITY 与服务器当前值不同。
5. 游标 UID 大于服务器当前最大 UID，说明邮箱状态无法按原高水位解释。

reset 不是静默忽略。poller 应在账户的安全可观察字段中记录一次分类信息，例如 `cursor_reset_initial`、`cursor_reset_legacy` 或 `cursor_reset_uidvalidity_changed`，但成功保存新游标后本轮仍属于成功。不得记录旧游标原文、账号或服务器响应。

UIDVALIDITY 改变后不尝试用旧 UID 猜测对应关系，也不回扫全部邮箱。这样会跳过 reset 前的历史邮件，但避免把整个邮箱重新导入并产生大量重复或过期通知。操作者若确实需要历史导入，应另做一次性、显式、离线工具，不属于本轮。

### 7.5 搜索、抓取和游标推进

续读规则：

1. 使用当前文件夹 UIDVALIDITY 验证游标。
2. 搜索所有大于游标 UID 的 UID。
3. 按数值升序取前 `APP_IMAP_BATCH_SIZE` 个。
4. 对选中 UID 使用 UID FETCH 获取完整 RFC 822 消息和必要元数据。
5. 每个成功转换的 envelope 使用对应 UID 构造稳定 `provider_message_id`。
6. `next_cursor.uid` 等于本轮成功返回批次中的最大 UID。没有新 UID 时保持原 UID。
7. poller 成功入库整个批次后一次性保存 `next_cursor`。

禁止按 `UNSEEN` 搜索。Seen 是用户邮箱状态，不是应用处理状态，会导致用户在其他客户端打开邮件后应用漏收。

### 7.6 MIME 和字符集语义

适配器必须使用 Python 标准库 `email` 的现代 policy 解析字节消息，不自行拼正则解析 MIME。

字段规则：

| `MailEnvelope` 字段 | 解析规则 |
| --- | --- |
| `subject` | 解码 RFC 2047 encoded words，缺失时为空字符串 |
| `sender` | 取规范化后的 `From` 展示值和地址，移除 CR/LF，缺失时为空字符串 |
| `recipients` | 合并 `To` 和 `Cc` 地址，保持出现顺序并去重，不包含 `Bcc` |
| `received_at` | 优先解析 `Date` 并转 UTC；缺失或非法时使用 IMAP INTERNALDATE；两者均不可用时该消息为协议错误 |
| `content` | 按下述正文选择和清洗规则生成纯文本 |

正文选择顺序：

1. 忽略附件和带 `Content-Disposition: attachment` 的 part。
2. `multipart/alternative` 优先选择非附件 `text/plain`。
3. 没有可用纯文本时，允许把 `text/html` 转为有界纯文本。只能做文本提取，不执行脚本、加载远程资源或保留可点击追踪 URL。
4. 其他 multipart 按出现顺序连接可读文本 part，中间使用单个换行。
5. 根据 part charset 解码。缺失 charset 时先按 UTF-8 严格尝试，再使用标准库安全替换策略，不能因单个非法字节泄漏原始 bytes。
6. 统一 CRLF 为 LF，移除 NUL 和不允许的控制字符。
7. 最终正文在适配器边界限制为 `mail.MAX_BODY_CHARS`，避免先构造无界大字符串。`mail_service.ingest_mail()` 的 12000 字符截断仍作为持久化防线。
8. 不将附件文件名、二进制、完整 header 或原始邮件写入日志。

如果消息可解析但没有文本正文，返回空 `content`，让现有规则分类按 subject 工作。单封 MIME 的非关键 header 损坏不应阻塞整个邮箱。

若整封消息无法解析到稳定 UID、时间或 RFC 消息结构，则本轮抛出类型化协议错误，不推进游标。它由 poll worker 下一轮重试。连续失败应通过现有 `polling_attempt_count` 和 `polling_last_error` 暴露给运维，不在适配器内部循环重试或跳过未知邮件。

### 7.7 IMAP 类型化错误

在 `qq_imap.py` 定义一个不含 secret 的基类和封闭错误分类。建议分类如下：

| 错误 | 分类 | 外层处理 |
| --- | --- | --- |
| `IMAPConfigurationError` | 永久 | 启动失败或生产 not ready |
| `IMAPAuthenticationError` | 永久 | 本轮失败，不自动高频重试，health 降级并要求人工修复授权码 |
| `IMAPTransportError` | 可重试 | 由下一次持久化 poll 周期重试 |
| `IMAPTimeoutError` | 可重试 | 由下一次持久化 poll 周期重试 |
| `IMAPProtocolError` | 可重试且有上限观测 | 本轮不推进游标，重复失败转运维处理 |
| `IMAPCursorError` | reset 可恢复 | 按 7.4 建立新基线，不抛给 worker |

异常只保存稳定 `kind`、可选命令阶段和安全服务器状态码。不得保存账号、授权码、完整服务器响应、邮件内容或游标原文。

### 7.8 IMAP 重试所有权

重试只能有一个所有者：`application/lifecycle.py` 的 `imap-poller` 周期。

1. IMAP 适配器每次方法调用只尝试一次连接和一组命令。
2. 底层 socket 或 IMAP client 不配置自动重连。
3. `_worker_loop` 按既有间隔进入下一轮，`poll_once()` 持久化失败计数和安全错误。
4. 鉴权或配置错误不得在一轮中重试。
5. 不新增内存退避 timer。若未来需要指数退避，应先给 `MailAccount` 增加持久化 `next_poll_at`，本轮不做。

## 8. OpenAI 兼容 DeepSeek LLM 适配器

### 8.1 建议文件和资源所有权

新增 `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py`。

该适配器：

1. 使用项目已有 `httpx2` 同步 client。
2. 构造时接收已验证的 base URL、`SecretStr` API key、固定模型和 timeout/limits。
3. 默认创建并拥有一个 HTTP client，也允许测试注入 client。只关闭自己创建的 client。
4. 在整个 Starlette lifespan 内复用同一个实例和连接池。
5. 不访问数据库，不执行 Tool，不决定 PendingAction，不拥有持久化重试。

不为邮件分析和 Conversation 分别创建客户端或模型实例。

### 8.2 规范 HTTP 请求

使用 OpenAI 兼容 Chat Completions 线协议：

```text
POST <normalized-base-url>/chat/completions
Authorization: Bearer <secret>
Content-Type: application/json
```

公共请求字段只依赖 OpenAI 兼容形状：

```json
{
  "model": "deepseek-flash",
  "messages": [],
  "tools": [],
  "tool_choice": "auto",
  "response_format": {"type": "json_object"}
}
```

上例是无敏感数据的结构示意，不是供应商真实 payload。实现不得发送邮件或用户数据到日志。

共同规则：

1. 非流式请求，v1 不实现 SSE。
2. 请求体只包含当前方法需要的字段，不发送供应商私有扩展。
3. HTTP transport `retries=0`。持久化业务层是唯一重试所有者。
4. `follow_redirects=false`，避免 Authorization header 被带到意外主机。3xx 视为 provider 配置错误。
5. 每个响应都必须先检查 HTTP status，再按严格 Pydantic schema 解析。
6. 只读取标准 `choices[0].message`、`content`、`tool_calls` 和必要的标准错误对象。忽略未知扩展字段，但不能依赖它们。
7. 不声明供应商 token 上限。请求输入继续由现有 12000 字符边界、最近消息限制、ToolResult 大小和 Agent 最大运行时约束控制。

### 8.3 邮件结构化输出

`analyze_job_mail(prompt)` 必须要求 JSON object，并把模型输出作为不可信边界数据处理：

1. 发送固定 system 指令，要求只输出与 `JobMailAnalysisInput` 对应的 JSON object。
2. user message 只放 `bounded_prompt()` 已产生的有界邮件数据。
3. 使用 OpenAI 兼容 `response_format={"type":"json_object"}`，不假设供应商支持额外的 JSON Schema 扩展。
4. 响应必须恰有一个 choice，message 必须有非空文本 content，且不得同时包含 tool call。
5. 对 content 做 JSON 解码，顶层必须是 object。
6. 使用 `JobMailAnalysisInput.model_validate()` 完成最终严格校验。
7. 禁止正则截取 JSON、自动补括号、二次调用模型修复、默认填充缺失业务字段或把非法枚举映射为 `OTHER`。

验证成功后，由 `mail_service.persist_analysis()` 保存结构化结果。`AnalysisMetadata.model_name` 必须改为实际配置的 `deepseek-flash`，`analysis_version` 和 `prompt_version` 在行为变化时显式递增。

### 8.4 Conversation 消息映射

`converse(prompt)` 将 `ConversationPrompt` 映射为标准 messages：

1. `system_prompt` 作为第一条 system message。
2. `session_summary`、active IDs 和必要上下文以单独、有界的 system/context message 表达。
3. `recent_messages` 按现有顺序映射为 user/assistant messages。
4. 当前 `user_message` 作为最后一条 user message。
5. `tool_results` 映射为与前序 tool call 对应的 tool messages。必须保留 call identity，不能把 ToolResult 拼进普通 user 文本。
6. tools 从 `agent/tools.py` 的现有 `ToolDefinition` 生成标准 function tool schema。名称、描述、输入 schema 由仓库定义提供，不在适配器重复维护另一份 registry。
7. v1 继续顺序 Tool Calling，单次 LLM 响应只接受一个 tool call。

### 8.5 Conversation 响应语义

标准响应只允许两种应用层结果：

```text
message.content 非空，且无 tool_calls
→ ConversationResponse(answer=...)

恰好一个 function tool_call，且无业务 answer
→ ConversationResponse(tool_call=ToolCallRequest(...))
```

处理规则：

1. `content` 和 tool call 同时为空，属于 malformed response。
2. 同时出现最终 answer 和 tool call，属于 contract violation，不猜模型意图。
3. 多于一个 tool call，属于 contract violation。当前 runtime 是顺序调用，不并行执行或静默丢弃其余调用。
4. tool type 必须是 `function`。
5. function name 必须非空，最终仍由现有 Tool Registry 检查是否允许。
6. arguments 是 JSON 字符串，解码后顶层必须是 object，键和值必须满足 `ToolCallRequest` 的当前类型边界。
7. 不因模型返回 Tool Call 就执行写入。现有 `WRITE_TOOL_NAMES` 路径只能生成 `PendingAction`，等待用户确认。
8. `finish_reason` 只做诊断，不覆盖消息结构本身。未知值不能驱动业务写入。

### 8.6 LLM 类型化错误

适配器统一抛出安全、可分类的 `LLMError` 子类或一个带枚举 kind 的冻结错误对象。建议分类：

| 错误 | 判定 | 可重试性 |
| --- | --- | --- |
| `LLMConfigurationError` | base URL、模型、client 配置无效 | 永久，启动失败 |
| `LLMAuthenticationError` | 401 或 403 | 永久，人工修复凭据或权限 |
| `LLMRequestRejectedError` | 400、404、422 等确定性请求拒绝 | 永久，修代码或配置 |
| `LLMRateLimitError` | 429 | 可重试，外层持久化调度 |
| `LLMProviderUnavailableError` | 5xx | 可重试，外层持久化调度 |
| `LLMTimeoutError` | connect/read/write/pool timeout | 可重试，外层持久化调度 |
| `LLMTransportError` | DNS、TLS、连接中断 | 可重试，外层持久化调度 |
| `LLMMalformedResponseError` | 非 JSON、缺 choice、错误字段类型 | 可重试到外层上限 |
| `LLMContractError` | JSON 不符合 `JobMailAnalysisInput` 或 `ConversationResponse` | 可重试到外层上限 |

错误对象只保留：

```text
kind
http_status?
safe_provider_code?
request_kind = mail_analysis | conversation
retry_after_seconds?
```

`safe_provider_code` 必须使用长度和字符白名单。`Retry-After` 只有在可安全解析和设置上限后才可保存，且只能供外层调度参考。禁止保存 prompt、邮件正文、用户消息、tool arguments、response body、Authorization header 或 URL query。

### 8.7 LLM 重试所有权

适配器和 HTTP transport 不重试。重试由现有持久化流程拥有：

1. 邮件分析由 `OutboxEvent` 发布状态和 `Mail.processing_state/next_retry_at` 驱动。
2. Conversation 由 `AgentRun` 的 attempt、state 和 `next_retry_at` 驱动。
3. 单次 `process_run` 内为完成一次用户任务而进行的下一轮 Tool Call 不是网络重试，仍受 `agent_max_tool_calls` 和 `agent_max_run_seconds` 限制。
4. 401、403、确定性 4xx 不应按普通瞬时故障重复调用。
5. 429、5xx、timeout、transport、malformed 和 contract error 可由各自外层状态机重试到既有上限。
6. 不允许 HTTP transport、适配器、worker 和 Outbox 四层同时重试。

当前 `event_pipeline.publish_once()` 只捕获宽泛的 `RuntimeError` 和 `ValueError`。实现时应让 LLM 类型化错误继承当前可捕获基类，同时逐步让上层按 `retryable` 分类决定 `RETRY_WAIT` 或终止失败，不能把鉴权错误重复三次后才暴露。

## 9. Factory、生命周期和关闭

### 9.1 OwnedAdapters

扩展 `infrastructure/adapters/factory.py` 的 `OwnedAdapters`，使其可以拥有可选的：

```text
imap
llm
embedding
chroma
```

同时保存需要关闭的具体资源，而不是假设每个协议都可关闭。IMAP 适配器每轮连接自行关闭，实例本身通常没有长期资源。LLM 适配器拥有 HTTP client，必须在 lifespan shutdown 关闭。

构造顺序建议为：

```text
验证 settings 配对和 production 要求
  ↓
构造 LLM HTTP client 和 LLM adapter
  ↓
构造 IMAP adapter
  ↓
构造已有 Embedding 和 Chroma
  ↓
返回 OwnedAdapters
```

任一步失败时，按构造逆序关闭已经创建的资源，并保留原始构造异常。清理异常只记录安全分类，不能覆盖根因。

### 9.2 注入规则

`application/lifecycle.py` 组装 `effective_adapters` 时采用逐能力优先级：

```text
显式 LifecycleAdapters 注入
  > factory 构造的生产 adapter
  > None
```

不存在第四层 fake fallback。

邮件分析和 Agent 必须引用 `effective_adapters.llm` 的同一个对象。测试应使用对象 identity 断言证明 factory 只构造一次，且 `_publish_cycle` 与 `_agent_cycle` 收到同一实例。

### 9.3 启动顺序

生产 lifespan 顺序：

1. 加载并验证配置。
2. 创建数据库。
3. 构造 OwnedAdapters。
4. 验证生产必需 adapter 已存在。
5. 执行数据库和 durable state startup recovery。
6. 启动 QQ runtime。
7. 标记外部能力 wiring ready。
8. 启动 IMAP、Outbox、Notification、Agent、Knowledge worker。
9. 开放 health ready。

如果必需 IMAP 或 LLM 构造失败，不能先启动 worker 后继续以部分功能运行。

### 9.4 关闭顺序

关闭时：

1. health 立即转为 not ready。
2. 取消 worker task group，等待当前线程调用按现有 AnyIO 取消语义退出。
3. 关闭 QQ runtime。
4. 关闭 OwnedAdapters，其中 LLM client 只关闭一次。
5. dispose database。

`close()` 应幂等。测试注入的外部 client 不由适配器关闭，factory 自建 client 必须关闭。

## 10. Readiness 与健康检查

### 10.1 区分进程健康和产品就绪

当前 `/health` 只检查数据库与 schema。实现后响应至少增加：

```json
{
  "runtime_mode": "local",
  "imap": "disabled",
  "llm": "disabled",
  "product_readiness": "local_only"
}
```

状态含义：

| 模式 | IMAP/LLM 状态 | HTTP | `status` | `product_readiness` |
| --- | --- | --- | --- | --- |
| local | 门控关闭 | 200 | `ready` | `local_only` |
| local | 门控开启且构造成功 | 200 | `ready` | `ready` |
| local | 门控开启但构造失败 | 503 | `not_ready` | `not_ready` |
| production | 两者都已配置并构造 | 200 | `ready` | `ready` |
| production | 任一缺失或构造失败 | 503 | `not_ready` | `not_ready` |

不得通过 health 主动登录 IMAP或发送 LLM 请求。readiness 证明配置完整、adapter 已构造、生命周期 wiring 已完成。外部在线可达性由最后一次 worker 结果和人工 live smoke 另行判断，避免 health 请求造成外部流量和凭据锁定。

可选增加安全字段 `imap_last_poll`、`imap_last_result`、`llm_last_result`，但只能来源于内存或数据库中的安全分类，不含请求内容。长期鉴权失败应使生产 `product_readiness` 降为 `degraded` 或 `not_ready`，具体阈值由测试固定。建议鉴权错误立即 not ready，瞬时网络失败保持 ready 但标记 degraded，以免短暂供应商抖动引发进程重启风暴。

### 10.2 Fake 的状态

如果显式注入 `FakeIMAPGateway` 或 `FakeLLM`：

1. 测试环境可以断言功能路径。
2. local demo 可显示 `fake`。
3. production health 必须为 503，不能把 fake 标记为 ready。
4. 生产 factory 永远不构造 fake。

## 11. 事务、幂等和 at-least-once 不变量

### 11.1 外部调用在事务外

以下调用必须在数据库事务外：

```text
IMAP connect/login/select/search/fetch/logout
LLM HTTP request
QQ request
Embedding request
Chroma operation
```

IMAP 流程：

```text
事务 A: 读取 MailAccount、account_key、cursor
COMMIT
外部 IMAP poll
每封邮件事务: Mail + MAIL_RECEIVED，幂等提交
事务 B: 保存 next_cursor、last_polled_at、attempt、safe error
COMMIT
```

LLM 邮件分析流程：

```text
事务 A: 读取 Mail 并构造有界 prompt
COMMIT
外部 LLM request
事务 B: JobMailAnalysis + JOB_MAIL_ANALYZED + ProcessedEvent
COMMIT
```

Conversation 已由 `turn_runtime._converse()` 通过 AnyIO thread 调用同步适配器。不要把 HTTP 请求移动到持久化 `ToolCall` 或 `AgentRun` 状态更新事务中。

### 11.2 IMAP at-least-once

允许以下崩溃窗口：

1. 邮件已从 IMAP 获取但未入库，游标不推进，下一轮重取。
2. 部分邮件已入库但批次游标未保存，下一轮重取整个批次，数据库唯一约束去重。
3. 全部邮件已入库但保存游标前崩溃，下一轮重取，仍由唯一约束去重。

因此系统提供 at-least-once 抓取和幂等业务入库，不是 exactly-once。

### 11.3 LLM at-least-once

LLM 请求可能因进程中断被再次执行。系统依赖以下持久化边界避免重复业务事实：

1. `JobMailAnalysis.mail_id` 唯一。
2. 首个 `JOB_MAIL_ANALYZED` 事件只创建一次。
3. PendingAction 通过 source、action type 和 proposal fingerprint 去重。
4. Conversation ToolCall 有 run 和 sequence 唯一边界。
5. 真正写操作由 `pendingActionId` 幂等，并只在用户确认后执行。

模型可能被重复调用，但不能因此绕过确认或制造重复 Application、JobEvent、KnowledgeDocument。

### 11.4 PendingAction 边界

真实适配器不能改变以下规则：

1. 邮件分析 `should_update=true` 只创建状态更新建议。
2. Conversation 返回 write tool call 只创建 `PendingAction`。
3. `resolved_arguments` 创建后冻结。
4. 确认和拒绝由确定性 Command Router 处理，不让 LLM判断。
5. 用户确认前 Application、JobEvent 和知识事实保持不变。
6. 重试只能重试已有持久化工作，不能创建一个绕过确认的新写入路径。

## 12. 测试先行实施计划

所有生产代码按 red、green、refactor 顺序实施。每个测试使用 Given、When、Then 结构，优先真实标准库对象、注入式 HTTP transport 和本地协议 server，不连接真实 QQ 邮箱或 LLM。

### 12.1 Settings 单元测试

在 `tests/unit/test_settings.py` 增加：

1. `APP_IMAP_AUTH_CODE` 和 `APP_LLM_API_KEY` 空字符串按缺失处理。
2. `APP_IMAP_PASSWORD` 被拒绝，错误不含 secret 值。
3. IMAP enabled 时 account 和 auth code 必须同时存在。
4. host 不是 `imap.qq.com`、port 不是 993、SSL 为 false、folder 不是精确 `INBOX` 均失败。
5. `APP_IMAP_ACCOUNT` 去除首尾空格且不能为空。
6. LLM enabled 时 base URL 和 API key 必填。
7. LLM base URL 非 HTTPS 失败。
8. model 不是 `deepseek-flash` 失败。
9. timeout、batch size、pool limits 的边界值。
10. production 模式缺 IMAP 或 LLM 门控时失败。
11. safe settings error 不包含账号、auth code、API key 或 URL query。

### 12.2 IMAP 游标单元测试

新增 `tests/unit/test_qq_imap_cursor.py`：

1. v1 cursor 编码再解析得到相同 UIDVALIDITY 和 UID。
2. 编码稳定且小于等于 255 字符。
3. 非法前缀、非法 base64、非 object JSON、缺字段、额外 schema version、负数被分类为 reset。
4. UIDVALIDITY 变化触发 reset。
5. cursor UID 大于服务器最大 UID 触发 reset。
6. 首次空邮箱建立 UID 0 基线。
7. 首次非空邮箱建立当前最大 UID 基线且不返回历史邮件。

### 12.3 MIME 单元测试

新增 `tests/unit/test_qq_imap_mime.py`，使用仓库内合成 `.eml` bytes：

1. UTF-8、GB18030 或声明 charset 的中文 subject/body 正确解码。
2. RFC 2047 多段 subject 解码。
3. multipart/alternative 优先 text/plain。
4. 只有 HTML 时生成有界纯文本，不请求远程资源。
5. 附件二进制不进入 content。
6. To 和 Cc 合并、去重、保持顺序。
7. 缺 Date 时使用 INTERNALDATE。
8. 非法 Date 和缺 INTERNALDATE 产生协议错误。
9. 空正文仍返回 envelope。
10. CRLF、NUL、控制字符清洗。
11. 超长正文边界。
12. header 注入字符不进入日志或结构化字段。

### 12.4 IMAP adapter 契约测试

新增 `tests/integration/test_qq_imap_adapter.py`，使用本地可控 IMAP 测试 server 或协议级 fake socket，覆盖真实 `imaplib` 命令交互，不 mock 业务方法返回值：

1. 创建 SSL 连接并校验证书和主机名。
2. 登录使用 account 和 auth code，测试证据只断言调用形状，不输出值。
3. 精确选择只读 `INBOX`。
4. 使用 UID SEARCH 和 UID FETCH，不使用 sequence number。
5. 每个 poll 新建并关闭连接。
6. UID 升序和 batch size。
7. 空批次仍返回可持久化 cursor。
8. auth、timeout、TLS、断连、BAD/NO 响应映射到正确错误类别。
9. FETCH 中途失败不返回可推进 cursor。
10. logout 失败不覆盖主要错误。

### 12.5 Poller 集成测试

扩展 `tests/integration/test_mail_pipeline.py`：

1. poller 将原 cursor 传给 gateway，并保存 `MailPollBatch.next_cursor`，不再保存最后一个 provider ID。
2. reset 空批次也能保存新 cursor。
3. 批次部分入库后失败时不推进 cursor。
4. 重取同批次不会重复 Mail 或 `MAIL_RECEIVED`。
5. gateway error 保留旧 cursor并记录安全分类。
6. envelope 按 UID 顺序入库。

### 12.6 LLM HTTP 单元和契约测试

新增 `tests/unit/test_openai_compatible_llm.py`，使用 `httpx2` mock transport 或本地 HTTP server：

1. base URL 规范化和 `/chat/completions` 路径只拼一次。
2. Bearer header 存在，但断言失败输出不含 key。
3. model 固定为 `deepseek-flash`。
4. 邮件分析发送 `json_object` response format，不发送 tools。
5. 合成结构化 content 能解析为 `JobMailAnalysisInput`。
6. 非 JSON、array 顶层、缺字段、非法 enum、非法 interview round、超长 summary 均产生 contract error。
7. Conversation messages 顺序正确，tool results 使用 tool role。
8. ToolDefinition 正确映射为 function tools。
9. 最终 answer 映射为 `ConversationResponse(answer=...)`。
10. 单个 tool call 的 arguments JSON 映射为 `ToolCallRequest`。
11. 空结果、多个 choices、answer 与 tool call 并存、多个 tool calls、非法 arguments 均被拒绝。
12. 3xx、400、401、403、404、422、429、5xx 映射正确。
13. connect/read/write/pool timeout 和 transport error 映射正确。
14. transport 请求次数严格为 1，证明 adapter 无内部重试。
15. response body、prompt、mail content、user message、tool arguments 不进入异常字符串。
16. 自建 client 关闭一次，注入 client 不被关闭。

### 12.7 Factory 和 lifecycle 测试

扩展 `tests/unit/test_factory.py` 和 lifecycle 测试：

1. 完整配置只构造一个共享 LLM adapter。
2. `_publish_cycle` 和 `_agent_cycle` 使用同一对象。
3. 每个 poll 使用同一个无状态 IMAP adapter 实例，但连接由 adapter 每轮新建。
4. 显式注入优先于 factory，未注入能力由 factory 补齐。
5. 部分配置安全失败，不创建 fake。
6. 后续 adapter 构造失败时，已创建 LLM client 被关闭一次。
7. shutdown 先停止 worker，再关闭 LLM client，最后 dispose database。
8. 重复 close 幂等。
9. production 中 fake 注入使 readiness 失败。

### 12.8 Readiness 测试

扩展 `tests/e2e/test_health.py`：

1. local 且门控关闭返回 200、`local_only`、`imap=disabled`、`llm=disabled`。
2. production 完整 wiring 返回 200 和 `product_readiness=ready`。
3. production 缺 IMAP 返回 503。
4. production 缺 LLM 返回 503。
5. adapter 构造失败不短暂返回 200。
6. fake adapter 在 production 返回 503。
7. health 不触发 IMAP 登录或 LLM HTTP 请求。
8. IMAP 鉴权失败后的产品状态降为 not ready，瞬时网络故障标记 degraded。

### 12.9 完整 E2E 验收路径

新增凭据无关 E2E，使用本地 IMAP server、合成邮件、本地 OpenAI 兼容 HTTP server、真实 SQLite 和 FakeQQGateway：

#### 路径 A，普通求职邮件

```text
应用启动
→ 首次 IMAP 建立基线
→ 测试 server 注入一封普通求职邮件
→ IMAP poll 入库 Mail + MAIL_RECEIVED
→ LLM 返回 should_update=false 的结构化结果
→ JOB_MAIL_ANALYZED
→ 创建一个 Notification
→ FakeQQGateway push 一次
→ 不创建 PendingAction、Application 或 JobEvent
→ 重启后不重复通知
```

#### 路径 B，状态更新型邮件

```text
新邮件
→ LLM 返回 should_update=true
→ 创建一个 PendingAction 和确认通知
→ 用户确认前 Application/JobEvent 数量为 0
→ 走确定性确认命令
→ 执行冻结参数
→ Application + JobEvent + OutboxEvent 原子提交
→ 重投邮件、事件和确认不产生重复事实
```

#### 路径 C，Conversation Tool Call

```text
QQ 入站用户消息持久化
→ 同一共享 LLM 返回一个只读 Tool Call
→ ToolResult 持久化
→ 同一 LLM 返回最终答案
→ FakeQQGateway deliver
→ AgentRun 完成
```

#### 路径 D，Conversation 写建议

```text
LLM 返回 UpdateApplicationStatus tool call
→ 只创建 PendingAction
→ 用户拒绝时不再次调用 LLM，不产生业务写入
→ 另一轮用户确认后才产生 Application/JobEvent
```

#### 路径 E，失败和重启

```text
IMAP fetch 中断，cursor 不推进
→ 重启后重取并幂等入库
LLM 首次 5xx，持久化 RETRY_WAIT
→ 到期后外层重试成功
→ 不重复 PendingAction、Notification 或 JobEvent
```

## 13. 数据库与迁移

v1 实现不需要新增数据库列：

1. `mail_accounts.polling_cursor` 已是 `VARCHAR(255)`，足以保存规范 cursor。
2. `polling_attempt_count`、`polling_last_error`、`last_polled_at` 已能承载轮询观测。
3. Mail、JobMailAnalysis、Outbox、ProcessedEvent 和 PendingAction 已有幂等边界。
4. LLM client 和 readiness wiring 属于配置与内存生命周期，不应写入新表。

因此不应为了本轮创建空 Alembic revision。仍必须运行 `uv run alembic check`，确认模型没有意外 schema 差异。

旧 cursor 的迁移采用 7.4 的运行时版本检测和显式 baseline reset，不做不可逆 SQL 批量清空。发布前应在停机窗口备份数据库，首次启动后检查每个活动账户的 cursor 已变为 `imap-v1.` 格式，并记录 reset 分类，不记录 cursor 原文。

如果实现过程中发现 255 字符不足，必须先停止并重新评审编码方案。不得擅自扩大字段或新增游标表，因为规范编码应远低于该上限。

## 14. 运维与可观察性

### 14.1 日志

沿用项目 structlog 和 `safe_external_error()`，只在决策点记录稳定事件名。建议事件：

```text
imap_poll_started
imap_cursor_reset
imap_poll_succeeded
imap_poll_failed
llm_request_succeeded
llm_request_failed
external_adapter_readiness_changed
```

允许字段：

```text
adapter
request_kind
account_key 的内部不可逆短标识
cursor_version
reset_reason
message_count
duration_ms
error_kind
http_status
safe_provider_code
attempt_count
```

禁止字段：

```text
APP_IMAP_ACCOUNT
APP_IMAP_AUTH_CODE
APP_LLM_API_KEY
Authorization header
完整 base URL query
完整 cursor
邮件 header/body
LLM prompt/response
Tool arguments/result 原文
```

### 14.2 运行检查

部署后日常检查沿用：

```bash
curl --fail http://127.0.0.1:8000/health
uv run python -m jobs_status_manager failures --include-stale
uv run python -m jobs_status_manager integrity-check
```

运维人员应额外确认：

1. production health 的 `imap`、`llm` 和 `product_readiness`。
2. 活动 MailAccount 的 `last_polled_at` 持续推进。
3. cursor 版本为 v1，不查看或复制完整值。
4. `polling_attempt_count` 增长与错误类别是否持续异常。
5. Mail 的 `RETRY_WAIT`、FAILED Outbox 和 stale AgentRun 数量。
6. LLM 401/403 立即处理，不通过重启循环掩盖。

### 14.3 备份和秘密轮换

上线前、旧 cursor reset 前和回滚前执行 SQLite backup 与 restore-check。轮换 QQ 邮箱授权码或 LLM API key 时：

1. 停止单进程应用。
2. 通过受保护环境文件或 secret store 更新单一规范字段。
3. 不同时保留旧新 secret 名。
4. 启动并检查 readiness。
5. 经人工批准后执行最小 live smoke。
6. 撤销旧 secret，清理 shell history 和临时文件。

## 15. 人工门控 live smoke

真实 QQ 邮箱和真实 LLM 验证不进入 CI，也不在本文编写任务中执行。必须由操作者明确批准测试时间窗、测试邮箱、测试邮件、LLM endpoint、secret 注入方式、预期副作用、停止条件和清理方案。

证据文件只记录时间、代码版本、配置 hostname、测试项、状态码、安全错误类别、脱敏关联 ID、`PASS/FAIL/BLOCKED/NOT_RUN` 和清理结果，不记录 secret、邮箱地址、邮件正文、prompt、response body 或 provider payload。

有序检查：

1. 确认 QQ 邮箱已单独开启 IMAP，并生成专用授权码，不使用网页登录密码。
2. 确认 `imap.qq.com:993` 证书和主机名校验通过。
3. 在空测试窗口启动应用，确认首次只建立基线，不导入历史邮件。
4. 发送一封无敏感内容的测试邮件，确认只入库一次并推进 cursor。
5. 重启进程，确认同一邮件不重复触发通知。
6. 使用操作者提供的 OpenAI 兼容 base URL 调用 `deepseek-flash` 邮件分析，确认结构化输出可解析。
7. 执行一次只读 Conversation，确认最终 answer 路径。
8. 执行一次只读 Tool Call，确认顺序 tool result 路径。
9. 执行一次写建议，确认用户确认前无 Application/JobEvent 变化。
10. 单独批准后确认该 PendingAction，验证真实写入只发生一次。
11. 模拟或等待一次可控瞬时失败，确认只有外层持久化状态重试，没有请求风暴。
12. 停止并重启，确认游标、Outbox、AgentRun 和 PendingAction 恢复。
13. 清理测试邮件、测试数据、临时环境文件和日志证据。

出现以下任一情况立即停止并标记后续步骤 `BLOCKED`：凭据泄漏、未知 endpoint、证书错误、历史邮箱被意外批量导入、重复写入、未经确认的业务变化、无法解释的多次 LLM 请求、错误日志含邮件或 prompt、清理失败。

## 16. 发布门和质量门

### 16.1 确定性质量门

```bash
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic current
uv run alembic check
git diff --check
```

还必须单独运行新增测试文件和完整 E2E。所有命令 exit code 为 0 才能进入 live smoke。

### 16.2 发布前检查

1. `APP_IMAP_PASSWORD` 已从 settings、`.env.example`、测试和文档引用中移除。
2. 代码中只有 `protocols.py` 定义 `IMAPGateway` 和 `LLMAdapter`。
3. `mail.py` 只保留邮件 schema，不再声明适配器协议。
4. factory 不会在生产构造 fake。
5. LLM HTTP transport retries 为 0。
6. 一次 lifespan 只创建一个 LLM adapter 和一个同步 HTTP client。
7. 每次 IMAP poll 新建一个 SSL connection。
8. production 缺 IMAP 或 LLM 时 health 为 503。
9. 所有外部调用均在数据库事务外。
10. 所有 write tool 和邮件状态建议仍受 PendingAction 确认门控制。
11. cursor 编码长度测试通过，首次和 UIDVALIDITY reset 语义通过。
12. 全 E2E 证明重试可能重复外部计算，但不重复业务事实。

### 16.3 上线门

| 门 | 条件 | 未通过时 |
| --- | --- | --- |
| G1 配置 | settings 测试、旧字段移除、secret 安全 | 不发布 |
| G2 协议 | IMAP/LLM contract tests 全绿 | 不发布 |
| G3 持久化 | cursor、Outbox、AgentRun、PendingAction 重启测试全绿 | 不发布 |
| G4 生命周期 | 构造、关闭、readiness、单实例测试全绿 | 不发布 |
| G5 本地 E2E | 五条凭据无关路径全绿 | 不进行 live smoke |
| G6 人工 smoke | IMAP 和 LLM 真实最小路径由操作者确认 | 保持生产门控关闭 |
| G7 观察期 | 无重复写入、无 secret 泄漏、失败率可解释 | 回滚真实 adapter |

## 17. 实施阶段

### 阶段 1，配置和规范协议

1. 先写 settings 失败测试。
2. 增加 runtime mode、门控和完整 IMAP/LLM 字段。
3. 删除 `imap_password`，改用 `imap_auth_code`。
4. 将协议统一到 `infrastructure/adapters/protocols.py`。
5. 在 `mail.py` 增加 `MailPollBatch`，删除重复协议。
6. 更新 fake 以满足新协议，但保持 test-only。

完成标准：settings、协议引用和 fake 测试全绿，尚不接真实网络。

### 阶段 2，IMAP cursor 和 MIME

1. 先写 cursor 和 MIME 单元测试。
2. 实现版本化 cursor codec。
3. 实现 MIME 转换。
4. 实现类型化 IMAP 错误。

完成标准：所有纯函数测试全绿，无网络依赖。

### 阶段 3，QQ IMAP 网络适配器

1. 先写本地 IMAP server 契约测试。
2. 实现每轮新 SSL connection。
3. 实现 login、readonly INBOX、UID 搜索和抓取。
4. 实现 initial/reset/batch 语义。
5. 修改 poller 保存 `next_cursor`。

完成标准：适配器契约和 mail pipeline 集成测试全绿。

### 阶段 4，OpenAI 兼容 LLM

1. 先写 HTTP 请求、结构化输出、Tool Call 和错误映射测试。
2. 实现共享 sync client。
3. 实现 `analyze_job_mail()`。
4. 实现 `converse()`。
5. 删除未使用的 `complete()`。
6. 接入实际 `AnalysisMetadata.model_name`。

完成标准：单次请求无内部重试，邮件和 Conversation 两条契约测试全绿。

### 阶段 5，Factory、lifecycle 和 readiness

1. 先扩展 factory 和 health 失败测试。
2. 扩展 `OwnedAdapters`。
3. 组装生产 IMAP 和共享 LLM。
4. 实现 local/production readiness。
5. 固定 shutdown 顺序和幂等 close。

完成标准：生产缺依赖不会误报 ready，测试证明单 LLM 实例和正确关闭。

### 阶段 6，完整 E2E、运维和发布

1. 完成五条本地 E2E。
2. 运行全部质量门和 Alembic 检查。
3. 备份和 restore-check。
4. 由操作者批准并执行 live smoke。
5. 进入短观察期，确认无重复业务写入或敏感日志。

## 18. 验收标准

实现只有同时满足以下条件才算完成：

1. QQ IMAP 只连接 `imap.qq.com:993`，强制 SSL 和证书校验，只读选择 `INBOX`。
2. 登录使用 `APP_IMAP_ACCOUNT` 和 `APP_IMAP_AUTH_CODE`，仓库不再接受 `APP_IMAP_PASSWORD`。
3. 每轮 poll 使用新连接，完成后关闭，不在多线程之间共享 IMAP connection。
4. cursor 是可解析的 `imap-v1` 不透明值，内容为 UIDVALIDITY 和最高持久化 UID，长度不超过 255。
5. 首次启动、旧 cursor、UIDVALIDITY 变化和 UID 回退均按文档建立新基线，不导入历史存量邮件。
6. MIME 能安全处理中文 header、multipart、HTML fallback、附件排除、时间 fallback 和大小边界。
7. poller 只在整个批次幂等入库成功后推进 adapter 返回的 cursor。
8. LLM base URL 必须显式配置，不写死未经核验的 DeepSeek endpoint。
9. 模型固定为 `deepseek-flash`，邮件分析和 Conversation 使用同一个 adapter/client/config。
10. LLM 使用 OpenAI 兼容非流式 Chat Completions，严格解析 JSON、answer 和单个 function tool call。
11. adapter 和 HTTP transport 不重试，邮件 Outbox/processing state 与 AgentRun 各自拥有持久化重试。
12. 类型化错误不包含 secret、邮件、prompt、tool arguments 或 provider body。
13. fake 只存在于测试和显式 demo，不能成为生产自动 fallback。
14. production 缺 IMAP 或 LLM 时 startup 或 health 明确失败，不能误报产品 ready。
15. 所有外部调用在数据库事务外。
16. 所有真实业务写入（Application、JobEvent、KnowledgeDocument 等）仍必须经过 PendingAction 用户确认；Mail、Analysis、Outbox、Notification 等流程记录不属于用户业务事实写入。
17. 完整测试证明 at-least-once 和幂等，不出现 exactly-once 声明。
18. 全部质量门、凭据无关 E2E 和人工批准 live smoke 有明确结果记录。

## 19. 回滚方案

### 19.1 发布前回滚

任一确定性测试或质量门失败，直接不发布。保留现有 fake demo 和数据库，不启用生产门控。

### 19.2 上线后回滚

如果 IMAP 或 LLM live smoke 失败：

1. 停止进程。
2. 将 `APP_IMAP_ENABLED=false` 或 `APP_LLM_ENABLED=false`，并将 `APP_RUNTIME_MODE=local` 仅用于受控诊断。
3. 不允许 production 模式靠 fake 继续对外声称可用。
4. 恢复发布前数据库备份只用于确认发生了不可接受的数据变化。普通 adapter 故障不应回滚已经正确提交的业务事实。
5. 保留 Mail、Outbox、AgentRun、PendingAction 的持久化失败状态，修复后由原状态机恢复。
6. 不把 cursor 手工改为猜测值。需要重新建立基线时，使用受控操作清空该账户 cursor，并接受不回扫历史邮件的语义。

### 19.3 代码回滚兼容性

本轮无 schema migration，代码回滚不会遇到数据库 downgrade。新 `imap-v1` cursor 对旧代码不可解释，因此回滚旧版本前必须停机并将活动账户 cursor 置空。旧版本若仍把 provider message ID 当 cursor，不得用于生产继续轮询，只可用于本地诊断。

## 20. 明确不在范围内

本轮不实现：

1. OAuth 或网页登录密码认证。
2. 多邮箱账户、多用户邮箱路由或邮箱管理界面。
3. `INBOX` 之外的文件夹、文件夹发现或跨文件夹游标。
4. IMAP IDLE、push mail 或长连接复用。
5. 历史邮箱批量导入工具。
6. 附件下载、附件解析或邮件原始 RFC 文件归档。
7. LLM streaming、SSE、多模型路由、模型自动 fallback 或供应商私有扩展。
8. 并行 Tool Calling 或一次响应执行多个 Tool Call。
9. 自动修复非法 LLM JSON 的第二次模型调用。
10. Redis、Celery、RabbitMQ、Kafka、队列服务或微服务拆分。
11. exactly-once 投递承诺。
12. 绕过 PendingAction 的自动状态更新或知识写入。

这些限制用于守住当前模块化单体的可靠性边界，不影响未来通过独立设计扩展。
