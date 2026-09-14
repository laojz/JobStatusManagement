# 已部署 Jobs Status Manager 全流程验证程序与执行记录

**日期：** 2026-09-14  
**范围：** 已部署 Jobs Status Manager 的进程、入口、数据库、QQ、LLM、IMAP、附件、知识库、投递、恢复与清理全流程  
**结论边界：** 本文件是可协作执行的验证程序，也是一次全新的执行记录。它不声明生产就绪，不继承历史测试或部署陈述为当前成功结果，也不改变现有需求状态。只有本次 Run ID 下重新执行并留下合规证据的步骤，才可填写当前结果。

> **安全执行优先级：** 服务器命令、日志、内部 ID、重试和故障排查必须遵循 [已部署人工验证与故障排查手册](../deployed-manual-validation-troubleshooting.zh-CN.md)。两份文档在操作安全上有差异时，以该较新的手册为准；本文件继续作为 31 个用例的依赖、状态和证据记录，不得借此改变任何初始状态。

## 1. 历史观察与本次结果边界

以下内容仅是先前观察，不是本次结果：

| 时间边界 | 先前观察 | 本次解释 |
|---|---|---|
| 先前 | `dig +short bot.panafish.icu A` 返回过 `8.148.15.121` | DNS 必须在 DF-02 重查，不得预填 `PASS` |
| 先前 | `curl -i https://panafish.icu/live` 返回过 `HTTP/2 502`，响应头含 `server: openresty` | 说明当时公网入口未到达健康应用，不代表当前状态 |
| 后续陈述 | 用户后来表示公网部署已经完成 | 尚未提供新的成功输出，DF-02 至 DF-04 及全部公网用例仍须重跑 |

候选 QQ 回调地址为 `https://panafish.icu/qq/callback`。实际路径由 `APP_QQ_WEBHOOK_PATH` 配置，不能从候选值推断部署值。

| 回调审批字段 | 记录 |
|---|---|
| 候选回调 | `https://panafish.icu/qq/callback` |
| 批准的替代回调 | `<无，或仅填写已批准的 HTTPS URL，不含查询参数>` |
| 最终批准回调主机名 | `<hostname>` |
| 最终批准回调路径 | `<path>` |
| 审批人和 UTC 时间 | `<operator-alias> / <YYYY-MM-DDTHH:MM:SSZ>` |

## 2. 执行规则与角色

1. 由一名主操作员控制开始、暂停、停止、重启、外部发送与最终判定。其他人员和代理只执行已分配且互不重叠的用例。
2. 全员使用同一个 `Run ID`。建议格式为 `DF-20260914-<UTC-HHMM>-<短随机标识>`，不得包含账号、openid 或主机私密信息。
3. 整个运行窗口保持一个应用进程、一个 SQLite 目标、一个 Starlette 监听器、一个 botpy `Client`。botpy 是进程内客户端，不得另开 HTTP 服务。
4. 部署工作目录、环境注入方式、`APP_DATABASE_PATH` 和私有数据目录必须保持不变。命令应在已部署工作目录与同一环境中运行，不得临时切换到另一个 `.env` 或数据库。
5. 代理所有权必须互斥。例如 QQ 接收代理不得同时负责投递重试，数据库证据代理不得启动或停止进程。
6. 出现结果歧义后，任何代理都不得独立重启、重试、重放或再次发送。先停止依赖用例，由主操作员确认外部副作用与下一负责人。
7. 假网关、临时 SQLite、`MockTransport`、本地测试和本地脚本只属于契约证据，不能记为真实 QQ、IMAP、LLM、embedding、Chroma、文件或图片能力的 `PASS`。

### 建议角色

| 角色 | 建议所有权 | 禁止事项 |
|---|---|---|
| 主操作员 `LEAD` | Run ID、身份表、波次放行、停止、重启、签字 | 不得在歧义后直接重试 |
| 平台代理 `PLATFORM` | DF-01 至 DF-06、DF-29、DF-30 | 不得启动第二进程或切换数据库 |
| QQ 接收代理 `QQ-IN` | DF-07 至 DF-10、DF-18 | 不得负责 QQ 发送重试 |
| 业务代理 `BUSINESS` | DF-11 至 DF-14、DF-27 | 不得直接写数据库制造结果 |
| 邮件代理 `MAIL` | DF-15 至 DF-17 | 不得记录邮件正文、地址或完整 cursor |
| 知识代理 `KNOWLEDGE` | DF-19、DF-20 | 不得在运行态重建 Chroma |
| QQ 投递代理 `QQ-OUT` | DF-21 至 DF-26 | 不得在歧义后盲发 |
| 运维证据代理 `OPS-EVIDENCE` | DF-28、DF-31、只读聚合核验 | 不得执行未列出的写 SQL 或手工 AgentRun 重试 |

## 3. 执行身份与能力快照

开始前填写。只写别名、主机名、版本、摘要和批准项，绝不写秘密。

| 字段 | 本次值 |
|---|---|
| Run ID | `<RUN_ID>` |
| 部署制品、镜像摘要或 commit | `<artifact-id-or-commit>` |
| 主机和运行时 | `<host-alias> / <OS> / <Python-version> / <SQLite-version>` |
| supervisor 或启动方式 | `<supervisor-alias-or-approved-launch-method>` |
| 部署工作目录 | `<deployed-working-directory-alias>` |
| 环境注入方式 | `<secret-manager-or-private-env-alias>` |
| 数据库目标别名 | `<DB_TARGET_ALIAS>` |
| Schema 目标 | `0008_qq_reply_targets` |
| 批准测试账号别名 | `<QQ_TEST_ACCOUNT_ALIAS>` |
| 批准收件人别名 | `<QQ_TEST_RECIPIENT_ALIAS>` |
| 批准邮箱账号别名 | `<IMAP_TEST_ACCOUNT_ALIAS>` |
| 公网应用主机名 | `panafish.icu` 或 `<approved-alternative-hostname>` |
| DNS 核验主机名 | `bot.panafish.icu` 和实际批准入口主机名 |
| QQ API 主机名 | `<approved-api-hostname>` |
| QQ Token 主机名 | `<approved-token-hostname>` |
| QQ 回调主机名和路径 | `<approved-hostname><approved-path>` |
| 能力快照 | `QQ=<enabled/disabled/unconfigured>; LLM=<...>; IMAP=<...>; embedding/Chroma=<...>; inbound-file=<...>; outbound-file=<...>; image=<...>` |
| 测试数据标签 | `<non-sensitive-semantic-labels-only>` |
| 预备份负责人 | `<owner-alias>` |
| 最终备份负责人 | `<owner-alias>` |
| 清理负责人 | `<owner-alias>` |
| 证据复核负责人 | `<owner-alias>` |

### 状态词汇

只能使用以下四个值：

| 状态 | 含义 |
|---|---|
| `PASS` | 本次 Run ID 下已执行，实际结果符合预期，证据和清理字段完整 |
| `FAIL` | 能力已批准并可用，动作已执行，但实现、状态或副作用不符合预期 |
| `BLOCKED` | 前置能力、审批、凭据、端点、测试账号或安全条件缺失，不能安全执行 |
| `NOT_RUN` | 尚未执行，或因上游停止而不应开始 |

QQ、LLM、IMAP、embedding/Chroma、入站文件、出站文件或图片能力被禁用或未配置时，相关用例必须记为 `BLOCKED`，不是 `FAIL`。上游尚未执行时，下游通常为 `NOT_RUN`。上游已确认能力缺失时，下游记为 `BLOCKED`。

## 4. 证据脱敏规则

证据中绝不记录以下内容：凭据、`Authorization` 头、签名、challenge token、完整 openid、完整 event/message/provider ID、provider 原始 payload、消息正文、prompt、LLM 响应、邮件正文、邮件地址、完整 IMAP cursor、附件 URL、URL 查询串、附件内容、证书私钥、数据库内容、备份内容。

内部 task ID 仅可按主手册 Phase 13 在批准的私有非记录终端中短暂使用，不得进入本台账、聊天、shell history、截图或复制日志。原始 failure/retry 输出不得显示。

允许记录：

- 主机名、路径模板、HTTP 状态、QQ `err_code`、退出码。
- 有上限的聚合数量和状态，例如 `ConversationMessage +1`、`PendingAction=PENDING`。
- 语义化输入标签，例如 `ordinary-chat-A`、`approved-job-mail-B`、`knowledge-doc-C`。
- 不可逆摘要，或截断 ID，例如 `sha256:<前 12 位>`、`evt:***a1b2`。不得同时记录可拼回完整值的多段。
- 脱敏日志来源名称和 UTC 时间窗口，不复制敏感日志正文。

发现任何敏感信息进入终端、日志、截图或证据时，立即停止。将当前用例记为 `FAIL`，后续外部用例记为 `NOT_RUN`，按事故流程处理泄漏，不得仅靠编辑文档掩盖。

## 5. 全局不变量

### QQ 与业务不变量

- 正常签名事件必须含 timestamp，并使用 Ed25519 对 timestamp 与原始 body 的实现规定输入验签。`op=13` URL challenge 由生产 transport 在普通签名校验前处理。
- 当前支持的入站类型是 C2C 文本和 C2C 文件。多附件不支持。
- 入站事实必须先持久化，再返回普通 ACK `{"op":12,"d":0}`。
- 重复身份按 event ID 或 message ID 任一命中判重，精确重投不能创建第二个 `ConversationMessage`、`AgentRun`、QQ 身份记录或 `UserFile`。
- 被动回复目标必须完整持久化 message ID 和 event ID，可用时包括正数 `msg_seq`。缺字段必须关闭失败，不能降级为主动发送。`BotpyQQGateway.reply(user_id, message_id, content)` 这个旧三参数方法固定拒绝，不能用它证明被动回复。
- 主动发送不得携带 message ID、event ID 或 `msg_seq`。
- 写操作必须先确认。拒绝不改变业务事实。确认产生实际状态变化时，`Application`、一个 `JobEvent` 和一个对应 `OutboxEvent` 在同一事务中生效。确认无变化时不创建 `JobEvent` 或该状态变化 Outbox 效果。
- 出站投递是 at-least-once，不宣称 exactly-once。只有明确将超时或连接中断持久化为 `AMBIGUOUS` 且不会自动重试的路径，才可执行歧义结果测试。当前通知投递路径可能把抛出的超时归为普通失败并进入 `RETRY_WAIT`，因此禁止在线注入超时；未先证明自动重试已关闭时，真实 provider 歧义用例必须记为 `BLOCKED`。

### IMAP 不变量

- 每次 poll 建立一条新的 SSL 连接，并在该次 poll 后清理。
- 初次 cursor 以当前最大 UID 建立基线，故意不回填历史邮件。
- IMAP gateway poll 失败，或 durable mail ingestion 完成前失败，不得推进 cursor。durable ingestion 成功后，cursor 可以正确推进；后续 consumer、LLM、proposal 或 notification 失败通过 durable task/event 状态恢复，不回滚 cursor。
- 相同 UID、邮件、consumer 或通知重放不能创建重复持久事实。
- 证据不得包含原始邮件、正文、地址或完整 cursor。

### 附件与知识不变量

- 仅支持 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`。必须先完成受限下载、MIME/扩展名/大小校验、私有存储和解析预览，再允许确认。
- URL 下载只允许受限 HTTPS 路径，必须执行 SSRF、超时、总 deadline、大小、MIME 与私网目标防护。证据不记录 URL、查询串或文件内容。
- `AddKnowledge` 确认后才创建关系型 `KnowledgeDocument` 与 `KnowledgeChunk`。SQLite 是事实源，Chroma 只是可重建索引。
- 索引状态应为 `PENDING -> INDEXING -> READY`，失败时为 `FAILED`。`REMOVED` 必须立即失去搜索资格，即使 Chroma 清理尚未完成。
- `rebuild-chroma` 只在应用停止或经主操作员确认完全静默时运行。

## 6. 波次、依赖与放行

| 波次 | 用例 | 建议负责人 | 放行条件 |
|---|---|---|---|
| W0 身份与安全 | DF-01 至 DF-06 | `LEAD`, `PLATFORM` | 身份表完成，同一数据库确认，预备份与 restore-check 成功 |
| W1 公网与 QQ 接收 | DF-07 至 DF-10 | `QQ-IN` | W0 通过，回调和账号获批，QQ 能力已配置 |
| W2 对话与业务 | DF-11 至 DF-14 | `BUSINESS` | DF-09 通过，LLM 可用，测试 Application 已批准 |
| W3 邮件 | DF-15 至 DF-17 | `MAIL` | W0 通过，IMAP 获批，DF-11 在需要 LLM 时通过 |
| W4 文件与知识 | DF-18 至 DF-20 | `QQ-IN`, `KNOWLEDGE` | DF-09 通过，文件获批，DF-19 的 embedding 门按能力判断 |
| W5 QQ 投递 | DF-21 至 DF-26 | `QQ-OUT` | DF-07 和对应业务前置通过，唯一收件人再次确认 |
| W6 并发、运维与恢复 | DF-27 至 DF-30 | `BUSINESS`, `PLATFORM`, `OPS-EVIDENCE` | 之前波次无未决歧义，维护窗口获批 |
| W7 清理与签字 | DF-31 | `LEAD`, `OPS-EVIDENCE` | 所有已执行用例有记录，外部副作用已明确 |

每个波次由主操作员书面放行。不得让多个代理并行操作同一会话、同一 PendingAction、同一通知、同一 provider 消息或同一恢复对象。

## 7. 安全命令约定

以下命令只在已部署工作目录和原部署环境中执行。`<...>` 是必须由主操作员从已批准记录中替换的非秘密占位符。不要在 shell 历史中写凭据。

```bash
dig +short bot.panafish.icu A
dig +short panafish.icu A
curl -i --connect-timeout 10 --max-time 20 https://panafish.icu/live
curl -i --connect-timeout 10 --max-time 20 https://panafish.icu/health
uv run python -m jobs_status_manager health
uv run python -m jobs_status_manager integrity-check
mkdir -p ./backups
uv run python -m jobs_status_manager backup './backups/jobs-<RUN_ID>-pre.db'
uv run python -m jobs_status_manager restore-check './backups/jobs-<RUN_ID>-pre.db'
uv run python -m jobs_status_manager backup './backups/jobs-<RUN_ID>-final.db'
uv run python -m jobs_status_manager restore-check './backups/jobs-<RUN_ID>-final.db'
```

仓库 `failures --include-stale` 可以输出完整 ID 和多行 `last_error`，因此绝不用于例行证据，也不得尝试用 shell `sed`、`awk` 或正则 sanitizer 过滤其原始输出。持久任务例行基线按主手册第 3.4 节记录 `/health` 的 `durable_tasks`、`failed_tasks`、`stale_tasks`。计数非零或需要 kind/state 明细时，只能使用批准的预脱敏 metadata source 或批准的只读聚合证据操作员/查询，只返回 kind、state、attempts、stale、可选 next_retry 和有界计数，不返回 ID、错误、行、正文或内容。

任何重试必须使用手册 Phase 13 的 dedicated owner、私有非记录终端、safe exact-ID source、no-history 变量调用、输出抑制和立即 `unset` 流程。这里不列出包含完整 ID 占位符的 retry 命令。没有安全明细或 exact-ID source 时，相关检查、重试和依赖的 restart 决策为 `BLOCKED`，不得回退到原始 CLI。

`rebuild-chroma` 不是例行安全命令。只有应用停止或 Lead 确认完全静默时，才按主手册 Phase 11 和 DF-20/DF-30 的限制执行。

直接 Alembic 命令不会读取 `.env`。只有数据库目标已由主操作员核对后，才可在相同工作目录执行：

```bash
APP_DATABASE_PATH='<approved-deployed-db-path>' uv run alembic check
APP_DATABASE_PATH='<approved-deployed-db-path>' uv run alembic current
```

不要发明数据库检查 CLI。需要数量或状态核验时，由批准的只读 SQL/证据代理对同一数据库执行脱敏聚合查询。查询只返回计数、枚举状态和截断摘要，不嵌入生产值，不输出正文或完整 ID，也不执行写事务。

## 8. DF-01 至 DF-31 执行矩阵

所有初始状态均为 `NOT_RUN`。执行后，使用第 9 节模板附加记录，不要只改矩阵状态。

| ID | 初始状态 | Owner | Depends on | Capability gate | 安全动作或命令 | 预期结果 | 记录证据 | Cleanup | Blocker / failure rule |
|---|---|---|---|---|---|---|---|---|---|
| DF-01 | `NOT_RUN` | `PLATFORM` | 无 | 已批准主机与进程查看方式 | 用 supervisor 的只读状态命令和监听器查看方式确认制品、启动方式、PID、端口、SQLite 目标别名。不得重启 | 仅一个应用进程、一个 Starlette listener、一个 botpy client，且指向身份表中的数据库 | supervisor 状态摘要、PID 截断值、监听端口、制品摘要、数据库别名 | 无 | 第二进程、第二监听器或数据库不符即 `FAIL` 并全局停止 |
| DF-02 | `NOT_RUN` | `PLATFORM` | DF-01 | 公网 DNS/TLS/NPM 已批准 | 运行两条 `dig` 和对批准主机的 TLS `curl`，检查证书主机、HTTP 入口及 NPM/openresty 转发 | DNS 返回批准地址，TLS 主机匹配，入口可到应用。仅入口 502 不算通过 | A 记录、证书主机摘要、HTTP 状态、server 头、UTC | 无 | 未知 IP、证书错配、意外主机或路由即停止公网波次 |
| DF-03 | `NOT_RUN` | `PLATFORM` | DF-02 | 公网应用入口可达 | `curl -i --connect-timeout 10 --max-time 20 https://panafish.icu/live`，替代主机须使用批准值 | HTTP 200，body 精确为 `{"status":"alive"}` | URL 仅保留主机和路径、HTTP 状态、content-type、body 固定状态字段 | 无 | 非 200、代理错误或 body 不符为 `FAIL`。不得从此推断依赖健康 |
| DF-04 | `NOT_RUN` | `PLATFORM` | DF-03 | 同一应用进程可答 | `curl -i --connect-timeout 10 --max-time 20 https://panafish.icu/health` | 只有数据库可读、schema 精确为 `0008_qq_reply_targets`，且产品能力 ready 或明确 local-only 时才应 HTTP 200。`durable_tasks=degraded` 仍可 HTTP 200。数据库不可用、缺 schema、非 head 或已配置能力不可用应 503 | HTTP 状态，`database`、`schema_version`、`product_readiness`、`durable_tasks` 与有界计数 | 无 | 把 health 当 QQ 连通性证明为证据错误。状态与语义不符为 `FAIL` |
| DF-05 | `NOT_RUN` | `PLATFORM` | DF-01 | 数据库路径获批 | 在原部署环境运行 CLI `health`；显式同一路径运行 `alembic check`、`alembic current` | CLI 报 `ready phase=6 schema_version=0008_qq_reply_targets`，Alembic 无新升级且 current 为 head，没有旁路数据库 | 命令、退出码、schema、数据库别名、工作目录别名 | 无 | 错误数据库、路径缺失、schema 不符或出现旁路 DB 即全局停止 |
| DF-06 | `NOT_RUN` | `PLATFORM` | DF-05 | 备份目录私有且目标文件不存在 | 先记录 `/health` 的 `durable_tasks`、`failed_tasks`、`stale_tasks`；计数非零或需要明细时，按手册第 3.4 节从批准的预脱敏 metadata source 或只读聚合证据取得允许字段；再运行 `integrity-check`，schema/integrity 有效后才运行 pre `backup` 和 pre `restore-check` | 零计数足以形成零任务基线；非零明细仅含 kind/state/attempts/stale、可选 next_retry 和有界计数；integrity 成功；备份使用 SQLite backup API；隔离 restore-check 成功，不替换线上 DB | health 三个聚合字段、安全来源的允许聚合字段、各命令退出码、integrity、备份文件别名和权限摘要 | 保留预备份到 Run 审核结束 | health 计数非零但无安全明细来源时详细检查及依赖决策为 `BLOCKED`；不得运行或 shell-filter 原始 failure CLI。integrity、备份、恢复检查失败，或备份目标指向线上 DB 时全局停止 |
| DF-07 | `NOT_RUN` | `QQ-IN` | DF-02, DF-04, DF-06 | `QQ=enabled`，凭据通过私有方式注入，API/Token 主机获批 | 由主操作员使用批准的预脱敏启动日志视图或日志 owner sanitized summary，并执行 `/health`。只核验 token/API 主机和配置路由，不打印值或原始日志 | 只创建一个 botpy client、一个 transport、一个 gateway；API/token 为批准 HTTPS 主机；配置回调路径就绪。health 不能单独证明 provider 连通 | SDK/制品版本、批准主机名、路由路径、启动退出状态、HTTP 状态、QQ `err_code` 如有、sanitized log source/window | 无 | 无安全预脱敏日志来源时日志内容检查为 `BLOCKED`。QQ 未配置为 `BLOCKED`；凭据不完整、主机意外、第二 client 或泄漏为 `FAIL` 并停止 QQ 波次 |
| DF-08 | `NOT_RUN` | `QQ-IN` | DF-07 | QQ 平台已批准 URL challenge 窗口 | 在 QQ 平台配置批准回调，触发真实 `op=13` challenge。不要在证据中复制 token、签名或 payload | transport 在普通签名校验前处理 challenge，平台验证成功，服务保持健康 | UTC、回调主机/路径、HTTP 状态、平台结果、脱敏 trace | 平台测试配置恢复为批准状态 | 未获 challenge 能力为 `BLOCKED`。token 泄漏、未知请求或响应错误为 `FAIL` |
| DF-09 | `NOT_RUN` | `QQ-IN` | DF-08 | 批准账号可发送 C2C 文本 | 从批准账号发送语义标签 `signed-c2c-text-A`。只读证据代理对前后状态做聚合检查 | 正常事件有 timestamp，Ed25519 对原始 body 验签通过；先持久化，再 ACK `{"op":12,"d":0}`；创建一条 ConversationMessage 和一个排队/执行 AgentRun，QQ 身份映射有界增长 | HTTP/ACK、签名接受结果、持久化先于 ACK 的日志顺序摘要、各表增量、截断 event/message hash | 标记测试会话供 DF-31 清理 | 持久化前 ACK、签名绕过、意外账号或 ACK 不符为 `FAIL` 并停止依赖用例 |
| DF-10 | `NOT_RUN` | `QQ-IN` | DF-09 | QQ 平台允许精确重投，或由平台正式 redelivery 机制触发 | 仅重投 DF-09 的同一真实 event 和 message，不新建相似消息 | event ID 或 message ID 任一重复即幂等；不创建第二个 ConversationMessage、AgentRun、QQ 身份或 UserFile | 重投 HTTP/ACK、四类对象前后计数、相同截断 hash | 无新增清理 | 无法精确重投为 `BLOCKED`。任一重复事实为 `FAIL` 并全局停止 |
| DF-11 | `NOT_RUN` | `BUSINESS` | DF-09 | `LLM=enabled` 且批准普通对话 | 发送语义标签 `ordinary-chat-B`，不含写请求、隐私或知识附件 | 一个主 AgentRun 顺序处理，LLM 返回普通答复，走被动回复路径，不创建业务写事实 | AgentRun 状态、工具调用有界计数、回复投递状态、Application/JobEvent/PendingAction 增量均为 0 | 标记消息供清理 | LLM 未配置为 `BLOCKED`。业务写入、越权工具或错误收件人为 `FAIL` |
| DF-12 | `NOT_RUN` | `BUSINESS` | DF-11 | LLM 与批准测试 Application 可读 | 分别发送应用列表、单个状态、历史查询的语义标签 | 只读返回与批准基线一致，可有查询 ToolCall/ToolResult，但 Application、JobEvent、PendingAction、Outbox 不增加 | 三类查询结果的语义摘要、工具名、业务表前后计数 | 无 | 任何业务写为 `FAIL`。缺测试 Application 为 `BLOCKED` |
| DF-13 | `NOT_RUN` | `BUSINESS` | DF-12 | 已批准可回滚测试 Application | 请求把测试 Application 更新到一个确实不同的批准状态，但此步不确认 | 创建唯一状态更新 proposal，用户看到确认提示；AgentRun/工具结果为 `WAITING_USER_CONFIRMATION`；确认前 Application、JobEvent 不变 | PendingAction +1、状态、确认码只记 hash、Application/JobEvent/状态变化 Outbox 增量 0 | 保留 proposal 给 DF-14 | 确认前发生业务变更为 `FAIL` 并全局停止 |
| DF-14 | `NOT_RUN` | `BUSINESS` | DF-13 | 主操作员批准确认、拒绝和 no-op 子用例 | A 确认 DF-13；B 创建另一 proposal 后拒绝；C 重复确认 A；D 对当前状态创建并确认 no-op | A 原子更新 Application，恰有一个 JobEvent 和一个状态变化 Outbox；B 不改业务事实；C 幂等无第二效果；D 完成但无 JobEvent/状态变化 Outbox | 每子用例 PendingAction 状态、Application 状态摘要、JobEvent/Outbox 增量、事务时间摘要 | 按批准计划恢复测试 Application，恢复也必须走确认路径并记账 | 任一非原子变化、拒绝有写、重复效果或 no-op 产生事件为 `FAIL` |
| DF-15 | `NOT_RUN` | `MAIL` | DF-06 | `IMAP=enabled`，邮箱与维护窗口获批 | 记录脱敏 cursor hash 前值，触发一次真实 poll，不发送新邮件 | 每次 poll 使用新的 SSL 连接；初次或 reset 以当前最大 UID 建基线，envelopes 为 0，不回填历史；连接清理完成 | 连接次数、reset 布尔值、邮件新增计数 0、cursor 前后 hash，不写完整 cursor | 无 | IMAP 未配置为 `BLOCKED`。历史回填、连接复用、原始邮件泄漏为 `FAIL` |
| DF-16 | `NOT_RUN` | `MAIL` | DF-15 | 批准发件通道和非敏感测试邮件 | 基线后发送一封语义标签 `approved-mail-C`，poll 成功后再 poll，并在批准方式下触发同 UID 重放 | cursor 从已持久化 UID 继续；成功 durable ingestion 一封；相同 UID/邮件/consumer/notification 不重复；gateway poll 或 ingestion 完成前失败不推进 cursor；ingestion 后下游失败不回滚 cursor，并从 durable task/event 状态恢复 | poll 次数、cursor hash 变化、durable ingestion 完成布尔值、Mail/Outbox/consumer/Notification 聚合增量与状态 | 删除或归档测试邮件按邮箱批准策略执行 | 无法制造新邮件为 `BLOCKED`。gateway/ingestion 前失败却推进 cursor 或重复事实为 `FAIL`；ingestion 后 cursor 正常推进且下游失败不属于 cursor `FAIL`，禁止重置或回退 cursor |
| DF-17 | `NOT_RUN` | `MAIL` | DF-16, DF-11 | `LLM=enabled` 仅用于 job-mail 子用例 | A 发送无职位词的非工作邮件；B 发送批准的工作邮件。观察 MIME 解析、分类、分析与路由 | A 保守分类为非工作邮件且不调用 LLM；B 调用 LLM 严格结构化分析。普通信息走通知，状态建议走确认提案，绝不自动改 Application | 分类、LLM 调用计数、分析状态、通知或 PendingAction 增量，均不含正文和地址 | 按批准策略清理两封测试邮件及相关通知 | LLM 缺失时 B 为 `BLOCKED`，A 仍可执行。自动状态写入或原始邮件泄漏为 `FAIL` |
| DF-18 | `NOT_RUN` | `QQ-IN` | DF-09 | `inbound-file=enabled`，五类非敏感单文件获批 | 逐一发送一个 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`，每条消息只含一个附件；另做一个未支持类型和一个多附件负例 | 支持文件经受限 HTTPS、SSRF、MIME、大小、timeout/deadline 检查后进入私有存储并解析；负例安全拒绝；不返回无界内容；多附件不处理 | 扩展名、MIME 类别、字节数区间、UserFile 状态和增量、存储权限摘要。不得记 URL/内容 | 删除批准测试上传及 `.part` 残留，保留关系记录按批准策略 | 能力缺失为 `BLOCKED`。私网访问、超限下载、URL/内容泄漏、多个附件被静默处理为 `FAIL` 并停止 |
| DF-19 | `NOT_RUN` | `KNOWLEDGE` | DF-18 | 已有一个批准解析文件 | 对一个文件发起 AddKnowledge。先检查预览和 proposal，再确认；另建一项后拒绝 | 确认前只有解析预览和 PendingAction；确认后创建一个关系型 KnowledgeDocument 及确定数量 KnowledgeChunk；拒绝不创建文档 | Parsed/UserFile 状态、PendingAction、KnowledgeDocument/Chunk 增量、文档类型和有界 chunk 数 | 保留确认文档给 DF-20；清理拒绝项 | 跳过预览或确认、拒绝仍创建文档、关系行不一致为 `FAIL` |
| DF-20 | `NOT_RUN` | `KNOWLEDGE` | DF-19 | `embedding/Chroma=enabled`，维护窗口允许停止或静默重建 | 观察新增文档索引 `PENDING -> INDEXING -> READY` 或 `FAILED`；执行 SearchKnowledge 和一次 RAG；确认 RemoveKnowledge 后立即重搜；仅在停止/静默后运行 `rebuild-chroma` 并再搜 | READY 文档可搜和用于 RAG；FAILED 有界记录；REMOVED 立即不可搜；SQLite 行可完整重建 Chroma，命令 typed counts 一致 | 状态序列、搜索命中计数/分数区间、RAG 语义标签、rebuild 输出计数、REMOVED 后 0 命中 | 保持最终生命周期为批准值，清理临时索引证据 | 能力缺失为 `BLOCKED`。运行态重建、REMOVED 仍可见、SQLite 与重建不一致为 `FAIL` |
| DF-21 | `NOT_RUN` | `QQ-OUT` | DF-09, DF-11 | QQ 被动文本发送已批准 | 使用持久化 ReplyTarget 完成一条被动文本回复，不调用旧三参数 `reply()` | target 包含批准 openid、message ID、event ID，可用时含正 `msg_seq`；缺字段关闭失败；客户端收到一条回复 | ReplyTarget 字段存在性布尔值、发送状态、QQ `err_code`、provider ID 截断值、收件人别名 | 清理测试消息按平台能力执行 | target 不完整、旧方法被当作证明、错误收件人或重复发送为 `FAIL` |
| DF-22 | `NOT_RUN` | `QQ-OUT` | DF-07, DF-06 | QQ 主动文本发送和唯一收件人已单独批准 | 主操作员触发一条语义标签 `proactive-text-D` 的主动推送 | 只使用批准 target openid，不带 message ID、event ID 或 `msg_seq`；客户端收到一次 | 字段缺失/存在性布尔值、结果、QQ `err_code`、截断 provider ID | 清理测试消息 | 未批准为 `BLOCKED`。带被动字段、意外收件人或歧义为 `FAIL` 并停止发送 |
| DF-23 | `NOT_RUN` | `QQ-OUT` | DF-22 | `outbound-file=enabled`，provider 明确批准普通文件，且存在已部署并批准的业务调用路径 | 生产 adapter 有 `send_file`，但无支持的 operator CLI 或通用 HTTP 调用入口；仅通过会自然到达生产 adapter 的现有已部署业务流发送一个小型非敏感普通文件 | provider 接受且客户端可见才通过。方法存在不等于能力支持或可调用入口 | 文件语义标签、扩展名、大小区间、QQ `err_code`、投递状态、截断 provider ID、批准业务流别名 | 删除本地测试 fixture 和平台消息按批准策略 | 无批准业务调用路径或能力未确认记 `BLOCKED`。禁止 ad hoc 脚本、未记录 endpoint 和直接 provider 调用；只凭方法存在判通过为证据错误 |
| DF-24 | `NOT_RUN` | `QQ-OUT` | DF-22 | `image=enabled`，provider 明确批准图片，且存在已部署并批准的业务调用路径 | 生产 adapter 有 `send_image`，但无支持的 operator CLI 或通用 HTTP 调用入口；仅通过会自然到达生产 adapter 的现有已部署业务流发送一个小型非敏感图片 | provider 接受且客户端可见才通过。方法存在不等于能力支持或可调用入口 | MIME 类别、大小区间、QQ `err_code`、投递状态、截断 provider ID、批准业务流别名 | 删除 fixture 和平台消息按批准策略 | 无批准业务调用路径或能力未确认记 `BLOCKED`。禁止 ad hoc 脚本、未记录 endpoint 和直接 provider 调用；未知收件人或内容泄漏为 `FAIL` |
| DF-25 | `NOT_RUN` | `QQ-OUT` | DF-21，以及 DF-22/23/24 中实际执行项 | 至少一项真实投递有 provider 结果 | 对照持久投递结果与批准客户端实际可见回执 | 成功项同时有 provider 成功分类和客户端可见结果，provider ID 只保留脱敏形式 | 每种媒介的持久状态、客户端可见布尔值、截断 provider ID | 无 | provider 成功但客户端不可见，或反向不一致，记 `FAIL` 并停止进一步发送 |
| DF-26 | `NOT_RUN` | `QQ-OUT` | DF-25 | 仅允许隔离确定性 adapter/stub，或已书面证明目标路径会持久化 `AMBIGUOUS` 且自动重试已关闭 | 优先使用现有确定性测试观察 retryable、permanent、ambiguous 分类。不得在线注入 timeout/transport 中断；通知投递的真实超时子项默认 `BLOCKED` | retryable 按对应持久策略处理；permanent 不重试；支持歧义分类的路径保留 `AMBIGUOUS`。任何路径都不宣称 exactly-once | 测试类型、分类、attempt 计数、next_retry 状态；确定性测试不能记为真实 provider `PASS` | 不产生外部副作用；若已有歧义记录，由主操作员人工核对 | 无安全隔离能力或未证明关闭自动重试时为 `BLOCKED`。在线故障注入、盲重试、重复外部效果或 exactly-once 声明为 `FAIL` |
| DF-27 | `NOT_RUN` | `BUSINESS` | DF-11 | LLM 可用，单一批准会话 | 在同一会话快速发送两个普通请求，不并行使用其他写测试 | 后一个 run 排队；同一 session 不同时存在两个 main `AgentRun=RUNNING`；按输入顺序完成 | 两个截断 run ID、状态时间序列、同会话 RUNNING 最大计数 | 清理测试会话 | 并发主 run 或乱序导致错误事实为 `FAIL` |
| DF-28 | `NOT_RUN` | `OPS-EVIDENCE` | DF-06，相关失败任务按需存在 | 只对已理解且允许的失败任务执行，并能保证 safe aggregate detail、safe exact-ID source 和私有非记录终端 | 按手册第 3.4 节使用 health/安全聚合基线。重试仅按手册 Phase 13 的 dedicated owner 流程，对 `outbox`、`notification`、`pending_action`、`knowledge_index`、`knowledge_cleanup` 相应记录重排；重试后仍通过批准的预脱敏 metadata 或只读聚合证据核对，不手工重试 AgentRun | health 提供 durable/failed/stale 计数；允许的明细来源不含 ID、错误或行；重试命令不把 ID 写入 history且原始输出被抑制；重试不绕过确认、不创建新业务事实；AgentRun 无安全手工 retry | health 三个聚合字段、安全来源的 kind/state/attempts/stale/可选 next_retry 和有界计数、脱敏 ID、退出码、业务事实增量 | 清理由原任务 owner 完成 | 零计数 baseline 可 `PASS`，重试子项为 `NOT_RUN`。无安全明细、safe exact-ID source、no-history/output-suppressed 流程或安全后验核对来源时，详细检查或重试为 `BLOCKED`；任何 AgentRun 手工 retry 建议为 `FAIL` |
| DF-29 | `NOT_RUN` | `PLATFORM` | DF-10, DF-26, DF-28 | 无未决歧义，维护窗口批准，supervisor 转发 SIGTERM，且非零持久任务已有安全聚合明细 | 记录 health/安全聚合基线，向唯一进程发送 SIGTERM，确认有序退出，再由批准 supervisor 用同一制品、环境和数据库启动一次 | workers 先停，botpy client、transport、database 各关闭一次；启动恢复 stale Notification/PendingAction/AgentRun/Knowledge 状态；ready 恢复且无重复事实或发送 | PID 前后截断值、退出/启动时间、health 三个持久任务聚合字段、批准安全来源的允许明细、关键表增量、发送增量 | 保持一个正常进程 | health 计数非零但无安全明细来源、未决歧义、错误 DB 或不能保证单进程时为 `BLOCKED`。不得用原始 failure CLI 决定重启；重复事实/发送或第二进程为 `FAIL` |
| DF-30 | `NOT_RUN` | `PLATFORM` | DF-29 | 私有备份路径和隔离 restore-check 可用 | 运行 final `backup`、`integrity-check`、final `restore-check`。如获批且应用停止/静默，可再运行 `rebuild-chroma` | SQLite 最终备份、线上 integrity 和隔离 SQLite restore-check 成功；该结果不证明上传文件已备份或恢复。Chroma 仅通过可选重建与 typed counts 验证，无 partial failure | 命令、退出码、SQLite 备份别名/权限、integrity/schema、上传文件备份范围说明、rebuild 计数 | 按保留策略保存 SQLite 备份；上传文件按独立批准策略处理；禁止放入仓库 | 把 SQLite 检查表述为完整运行数据恢复、备份覆盖现有文件、指向线上 DB、restore 替换线上 DB 或运行态 rebuild 均为 `FAIL` |
| DF-31 | `NOT_RUN` | `OPS-EVIDENCE` | 所有已执行用例 | 外部副作用均已明确，无进行中的操作 | 按批准清单清理测试消息、邮件、上传、fixture、临时证据；按手册第 3.4 节用 health/安全聚合基线检查残留任务，并检查 `.part`、监听器、测试制品和证据敏感模式 | 仅保留批准的备份、审计记录和一个正常服务进程；无秘密、原始 payload、测试附件、`.part`、未知任务或额外监听器 | 清理清单、health 三个持久任务聚合字段、必要时的批准安全聚合明细、残留计数、监听器数、证据 scrub 结果 | 本步即最终清理 | health 计数非零但无安全明细来源时残留任务检查为 `BLOCKED`，不得运行或过滤原始 failure CLI；清理失败、未知副作用、秘密残留或额外进程为 `FAIL`，不得签署完成 |

## 9. 单用例执行记录模板

每个 DF 用例复制一份。没有执行的字段保持占位符，不得用历史结果填充。

```markdown
### <DF-ID> <简短名称>

| 字段 | 记录 |
|---|---|
| Status | <PASS / FAIL / BLOCKED / NOT_RUN> |
| Run ID | <RUN_ID> |
| UTC time | <start/end ISO-8601 UTC> |
| Owner | <owner-alias> |
| Artifact | <artifact-id-or-commit> |
| Environment | <host/runtime/supervisor/workdir aliases> |
| Capability snapshot | <QQ/LLM/IMAP/embedding/Chroma/file/image states> |
| Depends on | <DF IDs and statuses> |
| Action | <exact command or approved semantic action label> |
| Exit / HTTP status | <exit code, HTTP status, QQ err_code if allowed> |
| Schema | <observed schema, expected 0008_qq_reply_targets> |
| Expected | <bounded expected result> |
| Actual bounded observation | <counts, states, booleans, no bodies> |
| Redacted ID | <hash prefix or truncated ID> |
| Durable side effects | <table deltas and state transitions> |
| Cleanup | <action, owner, result> |
| Limitations | <what this case does not prove> |
| First failed layer | <DNS/TLS/NPM/process/config/DB/schema/QQ/LLM/IMAP/file/knowledge/delivery/recovery/cleanup or none> |
| Relevant sanitized log source | <source alias and UTC window, no raw sensitive text> |
| Next owner | <owner-alias or none> |
| Handoff question | <single concrete question or none> |
```

若状态为 `BLOCKED`，必须写明缺少的能力或审批，不执行试探性 provider 调用。若状态为 `FAIL`，填写第一个失败层，不要把后续连锁错误分别记成新根因。

## 10. 全局停止条件

出现以下任一情况，主操作员立即冻结外部动作。当前用例按已观察事实记 `FAIL` 或 `BLOCKED`，未开始的依赖项记 `NOT_RUN`，因已确认能力缺失而无法运行的依赖项记 `BLOCKED`。

- 收件人、账号、endpoint、回调主机或路径与批准值不一致。
- 任何秘密、签名、challenge token、完整 ID、payload、消息、邮件、附件 URL/内容、数据库或备份内容泄漏。
- 普通 QQ ACK 早于入站持久化。
- 任一重复 event/message、邮件 UID、consumer、确认或重启产生重复业务事实。
- 文件下载不受 HTTPS、SSRF、MIME、大小、timeout、deadline 或私有存储边界控制，或响应包含无界内容。
- 外部投递副作用不明确。歧义期间禁止重启、重试、重放和再次发送。
- 清理失败，或发现未知残留任务、文件、监听器、进程、消息或测试账号影响。
- 实际数据库不是身份表中的目标，schema 不是 `0008_qq_reply_targets`，或出现旁路数据库。
- 同一 SQLite 上出现第二进程、第二监听器或第二 botpy client。

停止后，主操作员记录：停止 UTC、触发 DF、第一失败层、已知外部副作用、禁止动作、脱敏日志源、下一负责人。恢复执行必须生成书面放行，不得由单个代理自行决定。

## 11. 最终汇总

### 状态计数

| 状态 | 数量 | DF IDs |
|---|---:|---|
| `PASS` | `<count>` | `<ids>` |
| `FAIL` | `<count>` | `<ids>` |
| `BLOCKED` | `<count>` | `<ids>` |
| `NOT_RUN` | `31` | `DF-01..DF-31` |

上表初始值表示尚未执行。每次更新必须保证四类合计为 31。

### 未解决失败与阻塞

| DF ID | Status | 第一失败层或阻塞能力 | 已知影响 | 下一负责人 | 下一安全动作 |
|---|---|---|---|---|---|
| `<DF-ID>` | `<FAIL/BLOCKED>` | `<layer-or-gate>` | `<bounded-impact>` | `<owner>` | `<approved-action>` |

### 签字

签字只表示本程序按记录执行、证据已脱敏且未决项已列出，不等同于生产就绪、provider 全兼容或风险接受。

| 角色 | 别名 | UTC 时间 | 签字范围 | 结论限制确认 |
|---|---|---|---|---|
| Lead | `<alias>` | `<time>` | 波次、停止与汇总 | `<confirmed>` |
| Platform operator | `<alias>` | `<time>` | 进程、DB、备份、恢复 | `<confirmed>` |
| QQ-IN agent | `<alias>` | `<time>` | QQ 接收与附件 | `<confirmed>` |
| Business agent | `<alias>` | `<time>` | 对话与业务确认 | `<confirmed>` |
| Mail agent | `<alias>` | `<time>` | IMAP 与邮件 | `<confirmed>` |
| Knowledge agent | `<alias>` | `<time>` | 关系知识与 Chroma | `<confirmed>` |
| QQ-OUT agent | `<alias>` | `<time>` | 外部投递 | `<confirmed>` |
| Evidence reviewer | `<alias>` | `<time>` | 脱敏、计数与完整性 | `<confirmed>` |

## 12. 文档与仓库回归核验

这些命令仅用于核验文档和仓库回归，不是实时 provider、部署或生产就绪证据。应由代码维护者在仓库工作目录执行，并把结果与执行环境分开记录。

```bash
git diff --check
git diff -- docs/evidence/deployed-functional-flow-2026-09-14.md
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv lock --check
APP_DATABASE_PATH='<approved-migrated-test-db-path>' uv run alembic current
APP_DATABASE_PATH='<approved-migrated-test-db-path>' uv run alembic check
```

最后人工检查本文件和待提交 diff，搜索并确认没有凭据、`Authorization`、secret、token、signature、challenge 值、完整 openid、完整 event/message/provider ID、邮件地址、邮件正文、prompt/response、附件 URL/query/content、私钥、数据库内容或备份内容。自动模式搜索只能帮助定位，不能替代人工逐段审阅。

本文件创建时没有执行任何 provider 调用、部署命令、git 命令、测试或实时验证，因此 DF-01 至 DF-31 均保持 `NOT_RUN`。
