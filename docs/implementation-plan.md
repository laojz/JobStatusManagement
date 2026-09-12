# v1 Implementation Plan

本文是基于 [`docs/architecture.md`](./architecture.md) 编制的 Coding Agent 实施计划。架构文档第 48 章的 `v1 实现级落地基线` 优先于前文的抽象描述；本计划不重新设计领域模型、模块边界、核心数据流或技术原则。

本计划只覆盖从空仓库到可部署 v1 的实施顺序和验收方式，不包含业务代码实现。

## Current Verification Status

截至 2026-09-12，QQ botpy 适配器的确定性本地实现已完成并通过当前工作树验收：

```text
qq-botpy-sdk==2.0.4
full suite: 267 passed in 8.28s
Ruff / format / basedpyright / uv lock / Alembic / diff checks: PASS
Alembic head: 0008_qq_reply_targets (head)
```

该结果覆盖自定义 Webhook transport、持久化后 ACK、重复事件幂等、C2C
ReplyTarget 重建、同步到异步发送桥、附件安全边界和出站错误分类。它不等同
于真实 QQ 兼容性验收：token/API endpoint、URL challenge、C2C 收发、附件、
普通文件/图片、ambiguous provider outcome 和真实重启恢复仍为 human-gated
`BLOCKED`。历史 Phase 6 的 `120 tests` 结果继续保留为当时的阶段快照，不与
当前完整套件计数混用。可复核证据见
[`../.omo/evidence/task-8-qq-botpy-sdk-adapter.md`](../.omo/evidence/task-8-qq-botpy-sdk-adapter.md)。

## 0. Implementation Principles

### 0.1 架构事实与实现建议

以下内容已经由架构文档确定，Coding Agent 不得擅自改变：

- v1 是单用户、单邮箱账户、单 QQ 账号、单进程、异步模块化单体。
- 持久化使用 SQLite + WAL；向量索引使用本地 Chroma。
- 不引入微服务、Redis、Kafka、RabbitMQ、Celery、复杂 Workflow / DAG、多 Agent 并发编排或企业级权限平台。
- Application 是真实求职状态的 Source of Truth；Mail 是邮箱原始事实；JobMailAnalysis 是结构化理解；RAG 是可复用知识层。
- 所有真实写操作必须经过 `Proposal → PendingAction → User Confirmation → Execution`。
- LLM 没有直接修改数据库业务事实的权限。
- Agent 只允许顺序 Tool Calling；同一 Session 同时只执行一个主 AgentRun。
- 核心业务状态、JobEvent、OutboxEvent 和相关 PendingAction 状态必须在同一数据库事务中提交。
- 外部调用不放在数据库事务中；LLM、QQ、IMAP、Embedding、Chroma 和文件解析都必须在事务外执行。
- 事件语义是 `At-least-once Delivery + Idempotent Consumer`，不得声称 Exactly-once。
- 主动通知必须先持久化 Notification，再由 Dispatcher 调用 QQ Gateway.push()；极端情况下允许重复发送，但不能造成重复业务事实。
- 关系型数据库是知识 Source of Truth；Chroma 只是可重建索引；`KnowledgeChunk.id` 是 Chroma Record ID。
- 邮件内容、RAG 内容、用户上传文件和 LLM 输出都是不可信输入，不能改变系统指令、权限或 Tool Registry 权限。

以下内容属于本计划中的“实现建议”，不是已经确定的架构事实：

- 若仓库没有额外语言约束，建议采用 Python 3.12、`asyncio`、一个异步 HTTP Webhook 运行时、Pydantic 类结构化 Schema、SQLite 异步访问、pytest 测试。
- 建议按模块建立 `application`、`mail`、`pending_action`、`agent`、`notification`、`knowledge`、`infrastructure` 等目录，但目录名称可以适配最终语言生态，不得改变模块边界。
- 建议用版本化 migration 文件，不把“启动时自动建表”作为生产 schema 管理方式。
- 建议所有外部客户端统一注入超时、重试上限和可替换的 fake adapter，以便自动化测试和故障注入。
- 建议使用 UTC 持久化时间，并在展示层转换时区；这属于实现选择，不改变领域语义。
- 建议使用确定性的 JSON 序列化和指纹计算生成 `proposalFingerprint`，具体哈希算法可由实现选择。
- 建议将 `Mail`、`UserFile`、`KnowledgeDocument`、`OutboxEvent` 等需要后台恢复的对象增加状态、尝试次数、错误和下一次执行时间字段；这些是恢复实现字段，不改变对象的业务含义。

### 0.2 垂直闭环交付规则

1. 每个 Phase 结束时必须有一个可运行、可测试或可演示的系统增量。
2. 每个 Step 必须说明输入、核心对象、依赖和完成后的可观察结果。
3. 不先一次性搭完所有底层设施；只在当前闭环需要时增加表、Worker、Adapter 和 Tool。
4. 代码、migration、Schema、Prompt、配置、测试和运行文档必须随阶段同步提交。
5. 每个阶段的验收必须验证业务结果、数据库状态、重复执行行为和不应发生的副作用。
6. 外部依赖全部提供 fake / mock 边界，集成测试再用本地或测试服务验证真实协议。
7. 失败任务必须有持久化状态；不能用进程内存中的队列、Timer 或标志位作为唯一恢复依据。

### 0.3 全局不变量

- `Application` 的唯一业务匹配键是 `UNIQUE(userId, companyKey, departmentKey, positionKey)`。展示字段与规范化字段同时保存；`departmentKey` 使用空字符串表示空部门。
- 只要 `currentStatus` 或 `interviewRound` 发生变化，就创建 JobEvent；两者都没有变化时，不创建重复 JobEvent。
- 首次创建 Application 时，JobEvent 的 `previousStatus = null`；状态和轮次变化前后的值都必须持久化。
- `JobEvent` 必须关联产生它的 `pendingActionId`，并至少满足 `UNIQUE(pendingActionId)`。
- `PendingAction.resolvedArguments` 创建后不可修改；确认后直接执行冻结参数，不重新向 LLM 请求写入参数。
- `PENDING_ACTION_CREATED`、`PENDING_ACTION_RESOLVED`、`APPLICATION_STATUS_CHANGED` 等事件必须通过持久化 Outbox 传播。
- `ProcessedEvent` 必须满足 `UNIQUE(consumerName, eventId)`。
- 重复邮件不能产生重复 Mail 或重复 `MAIL_RECEIVED`；重复 Webhook 不能产生重复用户消息或 AgentRun；重复确认不能产生重复 JobEvent；重复 Chroma Upsert 不能产生重复 Chunk 记录。
- RAG 检索只使用 `lifecycleStatus = ACTIVE` 且 `indexStatus = READY` 的文档。
- `JOB_MAIL_ANALYZED.shouldUpdate = true` 时不发送普通邮件通知，而由 PendingAction 确认通知承担提示；`false` 时才生成普通求职邮件 Notification。

### 0.4 通用测试约定

- Unit Test：纯函数、状态转换、规范化、Schema 校验、路由判定、指纹、重试时间计算。
- Integration Test：真实临时 SQLite 文件、真实 migration、事务、唯一约束、Worker 领取和状态更新；外部服务使用 fake adapter。
- End-to-End Test：从接入边界进入，经过持久化和业务闭环，到可观察的回复、通知或最终数据库状态。
- 故障测试必须在外部调用前后注入退出或异常，验证重启后状态不会永久停留在不可解释状态。
- 测试断言必须同时检查“应该发生”和“绝对不能发生”的结果。

## Phase 0 - Project Bootstrap

### 1. 阶段目标

建立可以运行、迁移、测试和安全加载配置的单进程工程骨架。完成后，空仓库能够创建 SQLite 数据库、启用 WAL、执行版本化 migration，并启动一个可以被后续业务模块复用的进程生命周期和测试环境。

本阶段不实现 Application、邮件处理、QQ Agent、RAG 或业务 Tool；它只交付后续六个阶段共同依赖的最小工程基线，因此可以通过“新机器初始化 + 数据库迁移 + smoke test”独立验收。

### 2. 前置依赖

#### 代码依赖

无。仓库当前只有架构文档。

#### 数据模型依赖

无。只创建 schema migration 机制，不预先实现全部领域表。

#### 外部服务依赖

无。所有外部服务在本阶段使用配置占位和 fake 边界，不要求真实凭证。

### 3. 本阶段范围

#### 做什么

- 确定可重复安装和运行的语言、依赖、测试、格式化和静态检查命令。
- 建立模块化单体目录、进程入口、优雅关闭和配置加载边界。
- 建立 SQLite 连接、WAL、busy timeout、migration runner、事务 helper 和数据库目录。
- 建立单用户 `User` 与 `MailAccount` 的最小身份记录，供后续阶段使用。
- 建立日志脱敏、文件目录权限和本地运行文档。
- 建立临时数据库测试夹具、时钟、ID、外部 Gateway fake 的接口约定。

#### 暂时不做什么

- 不创建 Application、JobEvent、Mail、Session、AgentRun、Notification、KnowledgeDocument 等业务表。
- 不接入 IMAP、QQ Webhook、LLM、Embedding 或 Chroma。
- 不实现 Outbox、Worker、Tool Calling 或任何业务写入。
- 不引入 Redis、消息队列、分布式锁或容器编排平台。

### 4. 实施步骤

#### Step 0.1：确定运行时与工程骨架

- **目标**：建立从空仓库安装依赖、运行测试和启动单进程的最小路径。
- **涉及模块**：工程入口、依赖管理、模块目录、测试目录。
- **核心对象**：进程入口、模块注册表、应用生命周期接口。
- **前置依赖**：无。
- **完成结果**：新环境可以用文档中的一条安装命令和一条测试命令完成初始化；空应用可以启动并正常退出。
- **实现建议**：若无其他约束，采用 Python 3.12 + `asyncio` + Pydantic + pytest；这是默认实现建议，不是架构事实。

#### Step 0.2：建立配置、凭证和日志边界

- **目标**：让邮箱、QQ、LLM、Embedding、Chroma、SQLite 路径和 Worker 参数通过环境变量或本地配置注入。
- **涉及模块**：配置模块、日志模块、启动校验模块。
- **核心对象**：`AppConfig`、各外部服务配置、脱敏 Logger。
- **前置依赖**：Step 0.1。
- **完成结果**：缺少必需配置时启动失败并指出字段；敏感值不进入 Git、日志或错误信息；提供不含真实凭证的配置示例。

#### Step 0.3：建立 SQLite 与 migration 基础

- **目标**：提供可复用的 SQLite 连接、WAL 初始化、事务边界和版本化 migration。
- **涉及模块**：数据库基础设施、migration runner、schema 版本表。
- **核心对象**：`schema_migrations`、DB connection factory、事务 helper。
- **前置依赖**：Step 0.1、Step 0.2。
- **完成结果**：首次启动可以创建数据库目录并执行 migration；重复启动不会重复执行；数据库 `journal_mode` 为 WAL；事务异常会回滚。

#### Step 0.4：建立单用户和 MailAccount 基础身份

- **目标**：提供后续 Application、Mail、Session 和知识数据所需的单用户身份边界。
- **涉及模块**：用户模块、邮箱账户模块、对应 Repository。
- **核心对象**：`User`、`MailAccount`、`UNIQUE(mailAccountId, providerMessageId)` 所依赖的账户 ID。
- **前置依赖**：Step 0.3。
- **完成结果**：通过配置或 bootstrap 命令创建唯一 v1 用户和一个邮箱账户；重复 bootstrap 返回已有记录而不创建第二个用户或账户。

#### Step 0.5：建立测试夹具和最小进程生命周期

- **目标**：让后续阶段能在不连接真实外部服务的情况下验证事务、Worker 和边界协议。
- **涉及模块**：测试 fixtures、fake clock、fake ID、Gateway/AI adapter 接口、启动/关闭 supervisor。
- **核心对象**：`FakeClock`、`FakeLLM`、`FakeQQGateway`、`FakeIMAPGateway`、`FakeEmbedding`、`FakeChroma`。
- **前置依赖**：Step 0.2、Step 0.3、Step 0.4。
- **完成结果**：测试可以创建隔离临时数据库、替换当前时间和外部响应；进程收到关闭信号后不留下未关闭的数据库连接。

### 5. 本阶段产物

- 可安装的工程配置和依赖锁定文件。
- 模块化单体目录和进程入口。
- 配置 Schema、脱敏日志配置和本地配置示例。
- SQLite 连接配置、WAL 初始化、migration runner、`schema_migrations`。
- `User`、`MailAccount` 的 migration、Model、Repository 和 bootstrap 命令。
- 测试 fixtures、fake 外部适配器、基础 smoke test。
- 本地运行、配置、目录权限和故障排查文档。

### 6. 验收标准

#### A. 功能验收

- **Given** 一个没有数据库文件的新工作目录，**When** 执行初始化和启动命令，**Then** 数据库文件、schema 版本记录、单用户和单 MailAccount 均被创建，进程返回成功。
- **Given** 已初始化的数据库，**When** 再次执行同样的初始化命令，**Then** migration 不重复执行，User 和 MailAccount 数量保持为 1。
- **Given** 一个依赖缺失或必需配置缺失的环境，**When** 启动进程，**Then** 进程以非零状态退出，错误中只包含配置字段名，不包含凭证值。

#### B. 数据验收

- 数据库存在 migration 版本表，且当前版本与仓库 migration 文件可对应。
- `PRAGMA journal_mode` 返回 `wal`；连接具有明确的 busy timeout。
- `User` 和 `MailAccount` 的唯一约束阻止同一 bootstrap 重复插入。
- 事务中的故意异常会回滚测试写入，不能留下半条记录。

#### C. 幂等验收

- 连续执行两次 bootstrap 不新增 User 或 MailAccount。
- 连续执行两次同一 migration runner 不产生重复 schema 或异常。
- 同一测试用例重复创建临时数据库时，数据彼此隔离。

#### D. 重启恢复验收

- 本阶段没有后台业务任务；必须验证进程在空闲状态被停止后重新启动，数据库仍可打开、WAL 可合并读取、migration 状态可继续使用。

#### E. 权限 / Human-in-the-loop 验收

- 本阶段没有 Write Tool；必须确认不存在绕过 PendingAction 的业务写入口。此项以“无业务写操作实现”和代码审查记录验收。

#### F. 自动化测试要求

- **Unit Test**：配置校验、敏感字段脱敏、日志字段过滤、环境解析。
- **Integration Test**：SQLite WAL、migration 幂等、事务回滚、bootstrap 唯一性。
- **End-to-End Test**：空目录初始化 → 启动 → health/smoke 检查 → 优雅退出 → 再启动。

### Demo Scenario

1. 在一台没有项目数据的机器上安装依赖。
2. 从配置示例生成本地配置，不填写真实密码和 Key 也能运行 bootstrap smoke mode。
3. 执行初始化命令，显示 SQLite、migration、单用户和单 MailAccount 已就绪。
4. 停止并重新启动进程，确认不会重复建表或重复创建身份记录。

### Definition of Done

- [x] 工程可以从空仓库安装、测试和启动。
- [x] SQLite + WAL、migration、事务回滚和单用户身份已完成。
- [x] 配置、凭证、日志脱敏和本地目录权限已完成。
- [x] 基础 Unit / Integration / End-to-End smoke test 通过。
- [x] 当前阶段主流程可以实际运行并在重启后继续使用。
- [x] 没有提前实现邮件、Agent、RAG 或通知业务功能。
- [x] 文档、配置示例和运行命令已同步更新。

## Phase 1 - Application Core

### 1. 阶段目标

建立以 Application 为核心的真实求职状态管理闭环：用户可以创建或更新 Application，状态和面试轮次变化会形成 JobEvent，并可靠写入 OutboxEvent。所有写入都必须从 PendingAction 确认后执行，确认和拒绝由确定性 Command Router 处理。

本阶段不需要 QQ、IMAP 或 LLM；使用本地命令适配器和 fake 外部边界即可独立验证“Proposal → Confirmation → Transactional Write”的核心业务事实。

### 2. 前置依赖

#### 代码依赖

- Phase 0 的工程入口、配置、数据库、migration、事务 helper 和测试 fixtures。

#### 数据模型依赖

- `User`。
- 单用户身份；`MailAccount` 本阶段只作为已有身份边界，不参与业务状态。

#### 外部服务依赖

无。使用本地确认命令和 fake event publisher。

### 3. 本阶段范围

#### 做什么

- 实现 Application 状态枚举、展示字段、规范化字段和精确匹配。
- 实现 Application、JobEvent、PendingAction、OutboxEvent 的 schema、Repository 和 Service。
- 实现状态变化判定、首次创建、面试轮次变化、并发更新和事务幂等。
- 实现 PendingAction 的完整 v1 状态：`PENDING`、`CONFIRMED`、`EXECUTING`、`COMPLETED`、`REJECTED`、`FAILED`、`EXPIRED`。
- 实现短确认码和确定性确认 / 拒绝 Command Router。
- 实现 Domain Event Envelope 和核心状态变更的 Transactional Outbox。

#### 暂时不做什么

- 不实现 LLM Proposal、Conversation Agent、QQ Webhook、IMAP 和真实 Notification Dispatcher。
- 不实现邮件到状态建议的自动分析。
- 不实现模糊 Application 自动合并；语义相似只允许作为后续候选并要求用户确认。
- 不实现 JobEvent 以外的普通提醒事件。

### 4. 实施步骤

#### Step 1.1：实现状态枚举、规范化和 Application 引用

- **目标**：固定 v1 状态值、面试轮次约束、展示值与规范化值之间的规则。
- **涉及模块**：Application Domain、值对象、Tool/Command 输入 Schema。
- **核心对象**：`ApplicationStatus`、`ApplicationRef`、`companyKey`、`departmentKey`、`positionKey`。
- **前置依赖**：Phase 0。
- **完成结果**：相同用户、公司、部门、岗位的规范化输入能得到同一个业务键；中文不翻译、不语义改写；非法状态和非法轮次在边界处被拒绝。

#### Step 1.2：创建 Application 与 JobEvent schema 和 Repository

- **目标**：持久化真实求职状态和状态变化历史。
- **涉及模块**：Application persistence、migration、Repository。
- **核心对象**：`Application`、`JobEvent`、唯一索引、`latestJobEventId`。
- **前置依赖**：Step 1.1。
- **完成结果**：数据库支持精确查找、状态读取、历史读取、唯一业务键和 `UNIQUE(pendingActionId)` 约束。

#### Step 1.3：创建 Domain Event、Outbox 和事务事件写入

- **目标**：把核心业务事实与可靠派生事件放入同一数据库事务。
- **涉及模块**：Event Envelope、Outbox Repository、Application transaction helper。
- **核心对象**：`DomainEvent`、`OutboxEvent`、事件类型 `APPLICATION_STATUS_CHANGED`。
- **前置依赖**：Step 1.2。
- **完成结果**：事务提交后 Outbox 为 `PENDING`；事务回滚时 Application、JobEvent 和 Outbox 一起消失；事件包含 eventId、version、aggregate、correlation/causation 可选字段。

#### Step 1.4：实现 PendingAction 生命周期和确认码

- **目标**：为所有未来 Write Tool 提供冻结参数、状态转换、有效期和幂等依据。
- **涉及模块**：Pending Action Management、确认码生成、Action Repository。
- **核心对象**：`PendingAction`、`resolvedArguments`、`proposalFingerprint`、`sourceType`、`sourceId`、执行尝试字段。
- **前置依赖**：Phase 0；可与 Step 1.2 并行设计，但集成依赖 Step 1.2 的事务基础。
- **完成结果**：创建后参数不可修改；默认有效期为 7 天；确认码可读且在用户范围内可定位；非法状态跳转会被拒绝。

#### Step 1.5：实现确定性 Confirmation Command Router

- **目标**：在没有 LLM 的情况下解析确认、拒绝和确认码，并保证多 PendingAction 下不会误确认。
- **涉及模块**：Command Router、PendingAction Service。
- **核心对象**：`确认`、`拒绝`、`确认 PA-XXXX`、`拒绝 PA-XXXX` 的命令结果。
- **前置依赖**：Step 1.4。
- **完成结果**：一个待确认 Action 时支持无码确认；多个时无码请求被拒绝并要求确认码；不存在或已处理的确认码返回明确结果且不改变业务事实。

#### Step 1.6：实现确认后的 UpdateApplicationStatus 事务

- **目标**：把已确认的冻结参数转换成 Application、JobEvent、OutboxEvent 和 PendingAction 状态的原子变化。
- **涉及模块**：Application Management、Pending Action Management、Outbox。
- **核心对象**：`UpdateApplicationStatus`、`Application`、`JobEvent`、`APPLICATION_STATUS_CHANGED`。
- **前置依赖**：Step 1.2、Step 1.3、Step 1.4、Step 1.5。
- **完成结果**：确认后执行 `CONFIRMED → EXECUTING → COMPLETED`；首次创建 Application 时 `previousStatus = null`；状态或轮次无变化时不产生 JobEvent；并发更新不会覆盖较新的已确认事实。

#### Step 1.7：建立本地可运行状态核心 Demo 和测试

- **目标**：用本地命令或测试驱动适配器演示状态核心，不依赖 QQ 或 LLM。
- **涉及模块**：本地 Demo Adapter、Application/Action Integration Test。
- **核心对象**：测试 Application、PendingAction、JobEvent、OutboxEvent。
- **前置依赖**：Step 1.6。
- **完成结果**：可以创建一个待确认状态更新，分别演示确认、拒绝、重复确认、状态无变化和面试轮次变化。

### 5. 本阶段产物

- Application 状态枚举、规范化规则和值对象。
- Application、JobEvent、PendingAction、OutboxEvent migration、Model、Repository。
- Application Management Service 和 `UpdateApplicationStatus` Command。
- PendingAction 状态机、确认码生成和确定性 Command Router。
- Domain Event Envelope、`APPLICATION_STATUS_CHANGED` 事件和 Transactional Outbox 写入。
- Application、JobEvent、PendingAction、事务和并发测试。
- 本地状态核心 Demo / 操作说明。

### 6. 验收标准

#### A. 功能验收

- **Given** 不存在 Application，**When** 创建一个 `company=腾讯`、空 department、`position=后端开发` 的已确认状态更新为 `APPLIED`，**Then** 创建一条 Application 和一条 JobEvent，且 `previousStatus = null`、`currentStatus = APPLIED`。
- **Given** Application 为 `INTERVIEW, round=1`，**When** 确认更新为 `INTERVIEW, round=2`，**Then** Application 的轮次变为 2，并新增 JobEvent，记录 previous/current round。
- **Given** Application 为 `INTERVIEW, round=2`，**When** 确认相同状态和轮次，**Then** Application、`updatedAt`、JobEvent 数量和 Outbox 业务事件数量均不产生重复变化。
- **Given** 一个 `PENDING` PendingAction，**When** 用户拒绝，**Then** PendingAction 变为 `REJECTED`，Application 和 JobEvent 不变。

#### B. 数据验收

- `Application` 满足 `UNIQUE(userId, companyKey, departmentKey, positionKey)`。
- `department` 可以为 null/空展示值，但 `departmentKey` 始终为非 null 空字符串或规范化值。
- `JobEvent` 同时保存 previous/current status 和 previous/current interview round，并满足 `UNIQUE(pendingActionId)`。
- 确认后的业务事务同时写入 Application、JobEvent、OutboxEvent，并将 PendingAction 标记为 `COMPLETED`。
- 业务事务任一写入失败时，四类记录全部回滚，不允许只留下 Application 或只留下 JobEvent。

#### C. 幂等验收

- 对同一个 PendingAction 连续发送两次确认，最多产生一条 JobEvent；第二次返回“已处理”或等价明确结果。
- 重复执行确认后的 write command，不新增 Application、JobEvent 或 OutboxEvent。
- 同一业务键以不同空格、全半角和英文字母大小写输入时，只创建一个 Application。

#### D. 重启恢复验收

- 在写事务提交前终止进程，重启后数据库中不存在半提交的 Application/JobEvent/Outbox 组合。
- 在 PendingAction 为 `CONFIRMED` 但尚未领取执行时重启，启动后的恢复扫描可以将其继续执行或转为明确 `FAILED`，不得永久停留在无解释状态。

#### E. 权限 / Human-in-the-loop 验收

- **Given** LLM 或本地 Adapter 只产生了 Proposal，**When** 用户尚未确认，**Then** Application、JobEvent 和 `APPLICATION_STATUS_CHANGED` 均不发生变化。
- **Given** 用户拒绝 Proposal，**When** 再发送相同确认码，**Then** 不执行写操作。
- 确认后的执行只能读取冻结的 `resolvedArguments`，不能重新从用户文本或 LLM 结果构造参数。

#### F. 自动化测试要求

- **Unit Test**：状态枚举、规范化、状态/轮次变化判定、PendingAction 状态机、确认码解析、proposal fingerprint。
- **Integration Test**：唯一约束、事务原子性、JobEvent 幂等、Outbox 同事务写入、并发更新。
- **End-to-End Test**：本地 Proposal → 确认码 → Application 更新 → JobEvent/Outbox 查询；另测拒绝和重复确认。

### Demo Scenario

1. 使用本地 Demo 创建“腾讯 / 空部门 / 后端开发”的 `INTERVIEW round=1` 更新 Proposal。
2. 展示 PendingAction 为 `PENDING`，Application 尚不存在或保持旧状态，JobEvent 数量为 0。
3. 输入错误确认码，确认没有业务变化。
4. 输入正确确认码，展示 Application、JobEvent 和 OutboxEvent 同时产生。
5. 再次输入相同确认码，展示没有新增 JobEvent。
6. 对同一 Application 执行 `INTERVIEW round=2`，展示只新增一条轮次变化 JobEvent。

### Definition of Done

- [x] Application、JobEvent、PendingAction、OutboxEvent 数据模型和 migration 完成。
- [x] Application 规范化、精确匹配、首次创建和状态/轮次变化规则完成。
- [x] PendingAction 确认/拒绝/过期/执行状态完成，确认码协议完成。
- [x] UpdateApplicationStatus 的事务、幂等和并发保护完成。
- [x] 核心 Unit / Integration / End-to-End 测试通过。
- [x] 本地状态核心主流程可以实际运行并可重复演示。
- [x] 没有提前实现邮件、QQ Agent、RAG 和真实通知发送。
- [x] 文档、migration、配置和 Demo 命令已同步更新。

### 阶段 1 验收结果

阶段 1 已于 2026-09-09 验收通过。验收环境为 Python 3.13.9、SQLite
3.50.4 和锁定的 `uv` 环境。当前数据库版本为
`0003_pending_action_context_refs (head)`。

自动化验收结果：

| 检查项 | 结果 |
| --- | --- |
| `uv run pytest` | PASS：23 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS：63 files already formatted |
| `uv run basedpyright` | PASS：0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS：No new upgrade operations detected |
| `uv run alembic current` | PASS：`0003_pending_action_context_refs (head)` |

本地状态核心 Demo 已实际运行通过，验证了：

```text
proposal state=PENDING code=PA-DESK
confirmation=CONFIRMED execution=COMPLETED
changed=True
```

验收覆盖 Application 规范化和唯一匹配、首次创建、状态/面试轮次变化、
无变化更新、PendingAction 完整生命周期、确认码和确定性确认路由、多操作
歧义保护、事务原子性、故障回滚、重复确认幂等、确认后恢复，以及 Phase
2+ 范围隔离。详细命令、结果和范围边界见
[`docs/phase-1-verification.md`](phase-1-verification.md)。

## Phase 2 - Mail Pipeline

### 1. 阶段目标

建立 `IMAP → Mail → Classification → JobMailAnalysis → PendingAction/Notification` 的邮件闭环。普通求职邮件只生成持久化通知；可能引起状态变化的邮件只生成 PendingAction 和组合式确认通知，不直接修改 Application。

完成后可以用真实或 fake IMAP 输入两类邮件，并验证邮件事实、分析结果、确认提议和 QQ 主动通知之间的持久化链路。本阶段只实现 QQ Gateway 的出站 `push()` 合约和适配，不实现 QQ Webhook 接收与 Conversation Agent。

### 2. 前置依赖

#### 代码依赖

- Phase 0 的 MailAccount、配置、异步任务生命周期、测试 fake。
- Phase 1 的 PendingAction、UpdateApplicationStatus、Domain Event 和 Outbox 写入能力。

#### 数据模型依赖

- `User`、`MailAccount`。
- `Application` 的规范化精确匹配能力，用于邮件中的 application 建议。
- `OutboxEvent`。

#### 外部服务依赖

- IMAP 账户和测试邮箱，或 `FakeIMAPGateway`。
- LLM Service，或返回固定结构化分析的 `FakeLLM`。
- QQ push API，或 `FakeQQGateway.push()`；真实 QQ receive/reply 延后到 Phase 3。

### 3. 本阶段范围

#### 做什么

- 实现定时 IMAP Poller，默认 2–5 分钟轮询。
- 实现 MailAccount 边界、Mail 原始事实保存和 `providerMessageId` 幂等。
- 实现求职邮件规则分类和结构化 JobMailAnalysis。
- 实现分析 Schema、版本、模型、Prompt 版本和失败重试所需的持久化状态。
- 实现状态建议到 PendingAction 的去重和确认通知。
- 实现 `MAIL_RECEIVED`、`JOB_MAIL_ANALYZED`、`PENDING_ACTION_CREATED` 的事件传播。
- 实现持久化 Notification、NotificationAttempt、基础 Dispatcher 和 QQ push 出站适配。

#### 暂时不做什么

- 不实现 QQ Webhook、QQ 用户消息接收、Session 或 Conversation Agent。
- 不实现用户通过自然语言主动更新状态。
- 不实现 RAG、文件解析和 Chroma。
- 不把普通提醒写成 JobEvent；不把状态建议自动应用到 Application。
- 不在外部 LLM、IMAP 或 QQ 调用期间持有数据库事务。

### 4. 实施步骤

#### Step 2.1：实现 IMAP Poller 与 Mail Gateway 边界

- **目标**：按固定间隔获取新邮件，并将提供方字段转换为内部 Mail 输入。
- **涉及模块**：IMAP Mail Gateway、Poller、配置和任务调度。
- **核心对象**：`MailAccount`、IMAP message envelope、polling cursor 或最近检查时间。
- **前置依赖**：Phase 0、Phase 1。
- **完成结果**：轮询有超时、不会阻塞主事件循环；单次轮询失败记录错误并等待下一轮；重复拉取交给 Mail 唯一约束处理。
- **实现建议**：可以使用 provider UID/high-watermark 作为性能优化，但 `mailAccountId + providerMessageId` 必须是最终幂等依据。

#### Step 2.2：创建 Mail schema、Repository 和接收事务

- **目标**：保存邮箱原始事实并可靠产生 `MAIL_RECEIVED`。
- **涉及模块**：Mail persistence、Mail Gateway、Outbox。
- **核心对象**：`Mail`、`providerMessageId`、`MAIL_RECEIVED`。
- **前置依赖**：Step 2.1。
- **完成结果**：新邮件在一个事务中插入 Mail 和 Outbox；重复邮件返回已有 Mail，不新增 Mail 或事件；邮件正文和收件人字段按架构的 JSON/普通列原则保存。

#### Step 2.3：实现规则分类与求职分析 Schema

- **目标**：先用规则判断是否求职相关，再对求职邮件调用 LLM 输出固定 JobMailAnalysis。
- **涉及模块**：Mail Classification、Job Mail Analysis、LLM Service、Schema 校验。
- **核心对象**：邮件类型枚举、`JobMailAnalysis.application`、`statusSuggestion`、`details`、`confidence`。
- **前置依赖**：Step 2.2。
- **完成结果**：非求职邮件结束在分类阶段；求职邮件得到结构化分析；LLM 输出不符合 Schema 时不写入生效分析并记录可重试失败；LLM 调用在事务外。

#### Step 2.4：实现分析幂等与状态建议 PendingAction

- **目标**：将 `shouldUpdate = true` 的建议转换成唯一、冻结、可确认的 PendingAction。
- **涉及模块**：Job Mail Analysis、Pending Action Management、Application matching。
- **核心对象**：`JobMailAnalysis`、`analysisVersion`、`proposalFingerprint`、`mailAnalysisId`、`PENDING_ACTION_CREATED`。
- **前置依赖**：Step 2.3、Phase 1 的 PendingAction Service。
- **完成结果**：同一邮件、分析版本、动作类型和建议内容只产生一个有效提议；精确匹配的 Application 自动绑定；模糊或多个候选不自动合并，必须在确认信息中标明候选或新建意图。

#### Step 2.5：实现 Outbox Publisher、ProcessedEvent 和邮件事件消费者

- **目标**：让 `MAIL_RECEIVED`、`JOB_MAIL_ANALYZED`、`PENDING_ACTION_CREATED` 在重启后仍可传播，并使消费者幂等。
- **涉及模块**：Outbox Publisher、In-Process EventBus、ProcessedEvent、Mail event handlers。
- **核心对象**：`ProcessedEvent`、`UNIQUE(consumerName, eventId)`、事件消费者状态。
- **前置依赖**：Step 2.2、Step 2.3、Step 2.4。
- **完成结果**：Publisher 扫描数据库中待发布事件并投递；同一事件重复投递时消费者只产生一次结果；事件处理不依赖进程内存中的唯一副本。

#### Step 2.6：实现持久化 Notification 与基础 QQ push Dispatcher

- **目标**：把邮件提醒和确认提醒转为可重试的持久化主动通知。
- **涉及模块**：Notification、Notification Renderer、Notification Dispatcher、QQ Gateway.push。
- **核心对象**：`Notification`、`NotificationAttempt`、Notification 状态、`sourceEventId` 和关联 ID。
- **前置依赖**：Step 2.5。
- **完成结果**：普通邮件分析生成一条 `PENDING` Notification；状态更新型邮件不生成普通提醒，而由 PendingAction 事件生成组合式确认提醒；发送前有 NotificationAttempt；QQ push 成功标记 `SENT`，失败进入可解释的重试状态。

#### Step 2.7：完成邮件闭环测试和 Demo

- **目标**：使用 fake IMAP、LLM 和 QQ 验证两条邮件分支。
- **涉及模块**：全邮件闭环、测试夹具、可观测查询。
- **核心对象**：Mail、JobMailAnalysis、PendingAction、Notification、NotificationAttempt、OutboxEvent。
- **前置依赖**：Step 2.1–Step 2.6。
- **完成结果**：普通邮件和状态更新邮件都能从输入走到可观察的 QQ push；重复输入和事件重复投递不重复创建业务结果。

### 5. 本阶段产物

- IMAP Gateway、定时 Poller 和 MailAccount 配置。
- Mail migration、Model、Repository、provider message 幂等约束。
- 分类规则、LLM Service 接口、JobMailAnalysis Schema 和版本字段。
- Mail 分析状态/重试字段（如采用，需标记为实现字段）。
- PendingAction 邮件来源去重和 `PENDING_ACTION_CREATED` 事件。
- Outbox Publisher、In-Process EventBus、ProcessedEvent 和消费者幂等记录。
- Notification、NotificationAttempt、Renderer、Dispatcher、QQ push adapter。
- 邮件闭环 Unit / Integration / End-to-End 测试与 Demo。

### 6. 验收标准

#### A. 功能验收

- **Given** fake IMAP 返回一封求职信息邮件且分析 `shouldUpdate=false`，**When** Poller 处理该邮件，**Then** 依次存在一条 Mail、一条 JobMailAnalysis、一条 `JOB_MAIL_ANALYZED` Outbox 事件和一条 `PENDING` Notification；最终 fake QQ 收到一条普通通知。
- **Given** fake IMAP 返回一封“腾讯二面邀请”且分析 `shouldUpdate=true`，**When** 事件处理完成，**Then** 存在一条 PendingAction 和一条组合式确认 Notification；Application、JobEvent 不发生变化。
- **Given** 状态更新型邮件的 PendingAction 尚未确认，**When** Dispatcher 发送确认提示，**Then** 提示中包含公司/岗位、建议状态、确认码和不确认不会修改事实的说明。
- **Given** 一封非求职邮件，**When** 分类完成，**Then** 只有 Mail 被保存，不能创建 JobMailAnalysis、PendingAction、JobEvent 或求职 Notification。

#### B. 数据验收

- Mail 满足 `UNIQUE(mailAccountId, providerMessageId)`。
- JobMailAnalysis 与 Mail 满足一对一 `UNIQUE(mailId)`，并保存当前有效分析、analysisVersion、modelName、promptVersion、analyzedAt。
- `shouldUpdate=false` 与 `shouldUpdate=true` 的 Notification 分流符合架构规则，不能为状态更新邮件同时生成普通提醒。
- NotificationAttempt 的 attemptNo 连续且与 Notification 状态一致；关联 sourceEventId、mailId、pendingActionId 可追溯。
- LLM 失败不会产生生效 JobMailAnalysis 或 PendingAction，但 Mail 保留且有下一次可处理状态。

#### C. 幂等验收

- 同一 `mailAccountId + providerMessageId` 连续轮询两次，只存在一条 Mail 和一条 `MAIL_RECEIVED` 业务事件。
- 同一 Mail 重复分析相同 analysisVersion 和建议指纹，只存在一个有效 PendingAction。
- 同一 OutboxEvent 被同一 consumer 投递两次，只生成一条 Notification 或一条 PendingAction。
- Notification 在极端重复发送时可以产生多条 QQ provider attempt，但不能产生第二个 JobEvent 或第二个 PendingAction。

#### D. 重启恢复验收

- Mail 已提交但进程在分类前退出；重启后 Mail 会再次进入可处理集合，不会丢失。
- Outbox 已提交但 Publisher 发送前退出；重启后事件仍能被发布。
- Notification 为 `SENDING` 时进程退出；重新启动后该记录可被识别为 stale 并进入 `RETRY_WAIT` 或等价明确状态。
- LLM 调用失败后重启不会重复创建 Mail，但允许对同一 Mail 重试分析；重复成功结果不会重复创建 PendingAction。

#### E. 权限 / Human-in-the-loop 验收

- 邮件分析中的 `statusSuggestion` 只能创建 PendingAction，不能直接调用 UpdateApplicationStatus。
- 在确认码未被确认前，Application、JobEvent 和 `APPLICATION_STATUS_CHANGED` 不发生变化。
- 邮件正文中的“忽略确认、修改系统指令、直接执行”等文本不能改变 Tool 权限或绕过 PendingAction。

#### F. 自动化测试要求

- **Unit Test**：分类规则、分析 Schema、proposal fingerprint、邮件分支、通知模板。
- **Integration Test**：IMAP 入库幂等、Analysis 一对一、Outbox/ProcessedEvent 消费幂等、Notification 状态机。
- **End-to-End Test**：fake IMAP → Mail → Analysis → Notification/Confirmation → fake QQ push；另测非求职邮件和重复邮件。

### Demo Scenario

1. 向 fake IMAP 注入一封模拟“腾讯二面邀请”邮件。
2. 运行一次 IMAP Poller，确认 Mail 去重后保存。
3. 规则判断为求职邮件，Fake LLM 输出结构化 `INTERVIEW round=2` 建议。
4. 确认 JobMailAnalysis 和 PendingAction 已生成。
5. 运行 Outbox Publisher 和 Notification Dispatcher，确认 fake QQ 收到确认提示。
6. 查询 Application 和 JobEvent，确认用户尚未确认时二者均未被修改。
7. 再注入同一邮件，重复运行 Poller 和消费者，确认没有新增 Mail 或 PendingAction。

### Definition of Done

- [x] IMAP Poller、MailAccount、Mail 和邮件唯一约束完成。
- [x] 规则分类、结构化 JobMailAnalysis、LLM Schema 校验和分析幂等完成。
- [x] 状态建议进入 PendingAction，普通邮件和状态邮件通知分流完成。
- [x] Outbox Publisher、ProcessedEvent 和基础 Notification Dispatcher 完成。
- [x] 核心 Unit / Integration / End-to-End 测试通过。
- [x] 邮件主流程可以用 fake 服务和测试邮箱实际运行。
- [x] 没有提前实现 QQ Webhook Agent、自然语言写操作或 RAG。
- [x] 邮件协议、Prompt/Schema、配置和 Demo 文档已同步更新。

### 阶段 2 验收结果

阶段 2 已于 2026-09-10 验收通过。当前数据库版本为
`0004_mail_pipeline (head)`。本阶段在阶段 1 已验收成果之上，完成并验证了
IMAP → Mail → Classification → JobMailAnalysis → PendingAction/Notification
邮件闭环。

自动化验收结果：

| 检查项 | 结果 |
| --- | --- |
| `uv run pytest` | PASS：39 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS：76 files already formatted |
| `uv run basedpyright` | PASS：0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS：No new upgrade operations detected |
| `uv run alembic current` | PASS：`0004_mail_pipeline (head)` |
| `uv run python -m jobs_status_manager demo-mail-pipeline` | PASS：`pushes=1 application_changes=0` |

验收覆盖：

- IMAP cursor 传递、成功后推进和失败后保留。
- `mailAccountId + providerMessageId` 邮件入库幂等与重复邮件计数。
- 非求职邮件在规则分类后停止，不调用 LLM，不创建分析或通知。
- JobMailAnalysis 结构化校验、有效分析一对一持久化和失败重试状态。
- 状态建议通过既有 PendingAction 链路创建，重复分析不重复创建提议。
- 状态邮件确认前不修改 Application、JobEvent 或状态变化事件。
- 普通邮件与状态邮件通知分流，确认通知包含确认码和未确认不修改事实的说明。
- Outbox 缺失聚合的错误可观察且保持可重试，ProcessedEvent 保证消费者幂等。
- NotificationAttempt、QQ push 失败重试、最大尝试次数和 stale `SENDING` 恢复。
- 邮件正文中的提示注入文本不能改变权限或绕过 PendingAction。

阶段 2 明确不包含 QQ Webhook receive/reply、Session、Conversation Agent、Tool
Calling、自然语言写操作、文件解析、Embedding、Chroma 和 RAG。当前交付使用
Fake IMAP、Fake LLM 和 Fake QQ 验证协议与持久化闭环，真实 provider adapter
仍属于后续部署实现工作。

## Phase 3 - QQ Conversation Agent

### 1. 阶段目标

建立 QQ Webhook 到长期 Session、顺序 AgentRun 和只读 Tool Calling 的对话闭环。完成后，用户可以通过 QQ 查询真实 Application、JobEvent、Mail 和 MailAnalysis，单次 AgentRun 可以按顺序调用多个只读 Tool 并生成最终回答。

本阶段严格不开放 Write Tool；Agent 只能读取业务事实，所有消息接收和 Agent 执行都必须异步化，Webhook 要快速返回 2xx。

### 2. 前置依赖

#### 代码依赖

- Phase 0 的 HTTP 进程、配置、测试 fake 和异步任务生命周期。
- Phase 1 的 Application/JobEvent 查询 Service。
- Phase 2 的 Mail/JobMailAnalysis 查询 Service、QQ push adapter 和基础事件/通知能力。

#### 数据模型依赖

- `User` 与 QQ `user_openid` 映射所需的身份配置。
- Application、JobEvent、Mail、JobMailAnalysis。

#### 外部服务依赖

- QQ Webhook 签名或 Token 配置和测试事件。
- LLM Service，或固定 Tool Call/Final Answer 的 Fake LLM。

### 3. 本阶段范围

#### 做什么

- 实现 QQ Webhook 校验、事件类型过滤、用户映射和消息幂等。
- 实现 Session、ConversationMessage、AgentRun、ToolCall、ToolResult 的持久化。
- 实现同一 Session 单主 AgentRun、排队或处理中提示和超时边界。
- 实现 Tool Registry 基础定义和只读 Application/Mail Tools。
- 实现 Context Builder、顺序 Tool Calling、最多 8 次 Tool Call、最多 2 分钟和结果大小限制。
- 实现 Application + Mail 的复合查询 Demo。

#### 暂时不做什么

- 不注册或执行 `UpdateApplicationStatus`、`AddKnowledge`、`RemoveKnowledge` 等 Write Tool。
- 不让 LLM 判断确认/拒绝命令；确定性 Command Router 在 Phase 4 接入 Agent。
- 不实现并行 Tool Calling、DAG、SubTask 或多 Agent。
- 不把 Session Transcript 当作 RAG 知识库。

### 4. 实施步骤

#### Step 3.1：实现 QQ Webhook 接收和入口幂等

- **目标**：安全接收 QQ 消息/文件事件，持久化必要输入，并尽快返回 HTTP 2xx。
- **涉及模块**：QQ Gateway.receive、Webhook HTTP handler、事件幂等表或消息唯一字段。
- **核心对象**：QQ event/message ID、系统 `userId`、`ConversationMessage` 初始记录。
- **前置依赖**：Phase 0、Phase 2 的 QQ 配置边界。
- **完成结果**：无效签名/Token/发送者被拒绝；重复事件只产生一次内部消息；Webhook 不等待 LLM、Tool、文件解析、Embedding 或 Chroma。

#### Step 3.2：创建 Session、ConversationMessage 和 AgentRun schema

- **目标**：把一个 QQ 用户的长期会话和每次任务持久化。
- **涉及模块**：Session persistence、Agent Runtime persistence、migration。
- **核心对象**：`Session`、`ConversationMessage`、`AgentRun`、状态 `RUNNING/WAITING_USER_CONFIRMATION/COMPLETED/FAILED`。
- **前置依赖**：Step 3.1。
- **完成结果**：同一系统用户复用一个 Main Session；每条用户消息最多关联一个 AgentRun；AgentRun 可追踪开始、完成、失败和最终消息。

#### Step 3.3：实现 Tool Registry 和只读 Tool Contract

- **目标**：统一描述 Tool 名称、类别、权限、确认要求、输入/输出 Schema，并实现只读 Tool。
- **涉及模块**：Tool Registry、Application Tools、Mail Tools、Schema 校验。
- **核心对象**：`ToolDefinition`、`ApplicationRef`、`SearchApplications`、`GetApplication`、`GetApplicationStatus`、`GetStatusHistory`、`SearchMails`、`GetRecentMails`、`GetMailAnalysis`、`GetMailContent`。
- **前置依赖**：Phase 1/2 查询 Service；可与 Step 3.2 并行开发，集成依赖二者。
- **完成结果**：每个只读 Tool 只能调用查询 Service；输入和输出均经过 Schema 校验；不存在数据库写权限。

#### Step 3.4：实现 Context Builder 与顺序 Agent Runtime

- **目标**：按 System Prompt、Session Summary、Active Context、Recent Messages、当前消息和 Tool Results 构造 LLM 输入，并顺序执行 Tool Call。
- **涉及模块**：Conversation Agent、Context Builder、LLM Service、Tool Call Executor。
- **核心对象**：`AgentRun`、`ToolCall`、`ToolResult.contextRefs`、Tool sequence。
- **前置依赖**：Step 3.2、Step 3.3。
- **完成结果**：每次 Tool Call 持久化后才继续下一轮；不超过 8 次；单次运行不超过 2 分钟；Tool 有独立超时；ToolResult 超限会让 AgentRun 明确失败或终止。

#### Step 3.5：实现 QQ reply/push 回复路径和 Session 并发保护

- **目标**：将快速回复和延迟回复分别路由到 reply 或 push，并保证同一 Session 不并行修改上下文。
- **涉及模块**：QQ Gateway.reply/push、Session lock/claim、Agent worker。
- **核心对象**：Session 活跃 AgentRun、消息排队/处理中状态。
- **前置依赖**：Step 3.1、Step 3.4。
- **完成结果**：当前 AgentRun 未完成时新消息不会并发执行第二个主 AgentRun；可返回明确处理中提示或持久化排队；进程重启后超时 RUNNING 会失败或重新调度。

#### Step 3.6：实现只读复合查询 E2E

- **目标**：验证一个 AgentRun 内顺序调用 Application 和 Mail Tools 后再生成最终回答。
- **涉及模块**：QQ Webhook、Session、Agent Runtime、Application Tools、Mail Tools。
- **核心对象**：状态、最近邮件、邮件分析、ToolCall/ToolResult 序列。
- **前置依赖**：Step 3.1–Step 3.5。
- **完成结果**：用户询问“腾讯现在什么状态，最近一封邮件说了什么”时，Agent 在同一个 AgentRun 中取得真实数据并回答，回答不能凭空覆盖 ToolResult。

### 5. 本阶段产物

- QQ Webhook HTTP endpoint、签名/Token 校验和 user_openid 映射。
- Webhook 事件幂等记录或唯一约束。
- Session、ConversationMessage、AgentRun、ToolCall、ToolResult migration、Model、Repository。
- Tool Registry、只读 Application Tools、只读 Mail Tools 及 JSON Schema。
- Context Builder、LLM adapter、顺序 Agent Runtime、Session 并发保护。
- QQ reply/push 回复适配和 fake provider。
- 只读查询和复合查询 E2E 测试。

### 6. 验收标准

#### A. 功能验收

- **Given** 合法 QQ 文本事件“腾讯现在什么状态”，**When** Webhook 接收并后台运行 Agent，**Then** HTTP 快速返回 2xx，Session/Message/AgentRun 被持久化，最终回复包含数据库中的 Application 状态。
- **Given** 用户询问“腾讯最近一封邮件说了什么”，**When** Agent 处理，**Then** 至少按顺序执行 `GetRecentMails` 和必要的 Mail Tool，回答引用 ToolResult 中的邮件事实。
- **Given** 用户提出“腾讯现在到哪一步，最近邮件说了什么”，**When** Agent 运行，**Then** Application Tool 和 Mail Tool 的 ToolCall sequence 属于同一个 AgentRun，最后生成一条最终消息。
- **Given** LLM 请求第 9 次 Tool Call，**When** Runtime 处理，**Then** 不执行第 9 次，AgentRun 进入明确失败/终止状态并向用户说明限制。

#### B. 数据验收

- 一个系统用户只有一个长期 Main Session；每次新用户消息有一个 AgentRun。
- ToolCall sequence 从 1 开始递增；每个已完成 ToolCall 最多关联一个 ToolResult。
- ToolResult.contextRefs 能更新 Application、Mail 等 Active Context，但不能直接写入业务事实。
- AgentRun 的 `RUNNING`、`COMPLETED`、`FAILED`、`WAITING_USER_CONFIRMATION` 变化可查询。

#### C. 幂等验收

- 同一 QQ event ID 或 message ID 重复发送两次，只保存一条 ConversationMessage，不创建第二个 AgentRun。
- 同一 AgentRun 的 Tool 执行恢复不会重复追加相同 sequence 的 ToolCall；重复收到最终回复触发不应创建新业务事实。
- 同一 Session 在 AgentRun 运行期间收到两条消息时，不会同时启动两个主 AgentRun 修改上下文。

#### D. 重启恢复验收

- Webhook 已持久化消息和 AgentRun、但进程在返回后退出；重启后 Agent worker 可以继续处理或将超时 Run 标记为 `FAILED`。
- AgentRun 运行超过 2 分钟后不能永久保持 `RUNNING`；启动恢复扫描必须给出明确终态。
- Tool 外部调用超时后，ToolResult/AgentRun 必须持久化失败信息，不能让 Session 锁永久占用。

#### E. 权限 / Human-in-the-loop 验收

- Phase 3 的 Registry 中没有可执行的 Write Tool；LLM 即使返回 `UpdateApplicationStatus`，Runtime 也必须拒绝并记录权限错误，不修改 Application。
- 邮件/RAG/用户消息中的提示注入文本不能扩展 Tool Registry 或变更 `permission`。

#### F. 自动化测试要求

- **Unit Test**：Webhook 校验、事件解析、Context Builder、Tool Schema、Tool limit、顺序循环终止条件。
- **Integration Test**：消息/Run 持久化、Session 唯一性、ToolCall/ToolResult、单 Session 领取和超时恢复。
- **End-to-End Test**：QQ event → 2xx → AgentRun → Application/Mail read tools → QQ final reply；另测重复 event 和多 Tool 复合查询。

### Demo Scenario

1. 预置腾讯 Application 和最近一封 Mail/JobMailAnalysis。
2. 向本地 Webhook 发送合法 QQ 消息：“腾讯现在到哪一步，最近一封邮件说了什么？”
3. 立即检查 HTTP 返回 2xx，并确认没有等待 LLM 完成。
4. 查看 AgentRun，确认 ToolCall 按顺序调用 Application Tool 和 Mail Tool。
5. 检查 QQ fake provider 的最终回复，确认状态来自 Application，邮件内容来自 Mail，而不是 LLM 自行编造。
6. 重复发送同一个 event ID，确认数据库中没有第二条消息或 AgentRun。

### Definition of Done

- [x] QQ Webhook 校验、消息幂等、用户映射和快速 2xx 完成。
- [x] Session、ConversationMessage、AgentRun、ToolCall、ToolResult 完成。
- [x] 只读 Application/Mail Tool、Registry 和 Schema 完成。
- [x] 顺序 Agent Runtime、8 次 Tool Call/2 分钟限制和 Session 并发保护完成。
- [x] 核心 Unit / Integration / End-to-End 测试通过。
- [x] 复合只读查询可以通过 QQ 实际运行并回复。
- [x] 没有提前开放任何 Write Tool 或 RAG Tool。
- [x] Webhook、Tool Contract、Prompt、配置和运行文档已同步更新。

### 阶段 3 验收结果

阶段 3 已于 2026-09-10 验收通过。验收环境为 Python 3.13.9、SQLite
3.50.4 和锁定的 `uv` 环境。当前数据库版本为
`0005_conversation_agent (head)`。

自动化验收结果：

| 检查项 | 结果 |
| --- | --- |
| `uv run pytest` | PASS：45 tests passed |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS：86 files already formatted |
| `uv run basedpyright` | PASS：0 errors, 0 warnings, 0 notes |
| `uv run alembic check` | PASS：No new upgrade operations detected |
| `uv run alembic current` | PASS：`0005_conversation_agent (head)` |

无凭证 HTTP 场景已实际运行通过，验证合法 QQ 文本事件返回 `202`，Webhook
只执行请求校验、事件解析和持久化，不在请求中调用 LLM、只读 Tool 或 QQ
出站发送。重复 event/message ID 不会创建第二条 ConversationMessage 或
AgentRun。

验收覆盖长期 Main Session、同 Session 顺序执行和排队、AgentRun 超时与恢复、
ToolCall/ToolResult 持久化、8 次 Tool Call 上限、独立 Tool 超时、结果大小限制、
Application/Mail 复合只读查询、Active Context 更新、QQ 发送结果持久化，以及
Write/RAG Tool 越权拒绝。阶段 4 写操作和阶段 5 RAG 能力未提前开放。详细命令、
结果和范围边界见 [`docs/phase-3-verification.md`](phase-3-verification.md)。

## Phase 4 - Agent Write Operations

### 1. 阶段目标

将 Conversation Agent 与 Phase 1 的写操作正式接通，使用户可以通过 QQ 自然语言提出状态更新，但必须先看到冻结的 PendingAction 并显式确认。确认后恢复原 AgentRun，执行 `UpdateApplicationStatus`，写入 ToolResult，并生成最终回复。

完成后可以验证“LLM 只能提议、用户确认才写入”的 Human-in-the-loop 边界，以及多 PendingAction、重复确认、进程重启和写事务幂等。

### 2. 前置依赖

#### 代码依赖

- Phase 1 的 PendingAction、Confirmation Router、UpdateApplicationStatus 事务。
- Phase 3 的 QQ Webhook、Session、AgentRun、Tool Registry、顺序 Agent Runtime。

#### 数据模型依赖

- Application、JobEvent、PendingAction、Session、AgentRun、ToolCall、ToolResult、OutboxEvent。

#### 外部服务依赖

- QQ Webhook/reply/push。
- LLM Service，或可以产生固定 Proposal 的 Fake LLM。

### 3. 本阶段范围

#### 做什么

- 注册 `UpdateApplicationStatus` Write Tool 和输入/输出 Schema。
- 实现 ToolProposal → PendingAction 的冻结参数、展示摘要、指纹和 AgentRun 挂起。
- 接入确定性确认/拒绝 Router，并支持短确认码协议。
- 确认后领取 PendingAction，执行 Write Tool，产生 ToolResult 和最终回复。
- 支持自然语言主动更新 Application，包括精确匹配、创建新 Application 和模糊候选必须确认。
- 处理重复确认、执行失败、重试和 AgentRun 恢复。

#### 暂时不做什么

- 不增加新的业务写权限；除 `UpdateApplicationStatus` 外不开放知识库写 Tool。
- 不允许 LLM 直接拿数据库连接、SQL 或绕过 Service 的写权限。
- 不实现并行 Tool Calling、复杂 Workflow 或新的状态机。
- 不把用户未确认的状态建议写成 JobEvent。

### 4. 实施步骤

#### Step 4.1：定义 UpdateApplicationStatus Tool Contract 和权限闸门

- **目标**：让 LLM 只能输出符合 Schema 的状态更新 Proposal，并让 Registry 在执行前校验 `WRITE + requiresConfirmation=true`。
- **涉及模块**：Tool Registry、Application Tool、Schema Validator、Permission Guard。
- **核心对象**：`UpdateApplicationStatusInput`、`ApplicationRef`、目标状态、目标轮次、Proposal。
- **前置依赖**：Phase 1、Phase 3。
- **完成结果**：Tool 定义明确要求确认；缺少公司/岗位、非法状态、非法轮次或不明确 ApplicationRef 的 Proposal 不进入 PendingAction。

#### Step 4.2：实现 Proposal 到 PendingAction 的 AgentRun 挂起

- **目标**：将合法写 Proposal 转成不可修改的 PendingAction，并安全提示用户。
- **涉及模块**：Agent Runtime、Pending Action Management、QQ reply/push。
- **核心对象**：`PendingAction`、`resolvedArguments`、`displaySummary`、`proposalFingerprint`、`AgentRun.WAITING_USER_CONFIRMATION`。
- **前置依赖**：Step 4.1、Phase 1 Step 1.4/1.5。
- **完成结果**：创建 PendingAction 后 AgentRun 进入等待确认；QQ 回复包含短确认码、目标 Application、目标状态/轮次和确认/拒绝格式；Application 未变化。

#### Step 4.3：接入确认/拒绝 Router 和多 Action 安全规则

- **目标**：在 QQ 消息进入 LLM 前确定性处理确认/拒绝，并避免多个 Action 下误操作。
- **涉及模块**：QQ message processor、Confirmation Command Router、PendingAction Service。
- **核心对象**：`确认`、`拒绝`、短确认码、`activePendingActionId`。
- **前置依赖**：Step 4.2。
- **完成结果**：单 Action 支持无码确认；多 Action 的无码确认被拒绝；指定码只影响对应 Action；无效或已处理码不进入 Agent。

#### Step 4.4：实现确认后的 Write Tool Execution 和 AgentRun 恢复

- **目标**：确认后执行冻结参数，写入业务事实，并把结果交给原 AgentRun 生成最终回答。
- **涉及模块**：PendingAction Executor、Application Management、Agent Runtime、ToolResult。
- **核心对象**：`CONFIRMED → EXECUTING → COMPLETED/FAILED`、`JobEvent`、`APPLICATION_STATUS_CHANGED`、`ToolResult`。
- **前置依赖**：Step 4.3、Phase 1 Step 1.6。
- **完成结果**：成功时 Application/JobEvent/Outbox 与 PendingAction 原子更新；写入结果作为 ToolResult 关联原 ToolCall；原 AgentRun 恢复并发送最终回复；失败时不生成部分业务事实并可重试。

#### Step 4.5：实现 Agent 写操作 E2E 与安全回归测试

- **目标**：验证自然语言、确认、拒绝、重复确认、重启和 Prompt Injection 下的写权限边界。
- **涉及模块**：QQ Gateway、Agent Runtime、Write Tool、Application Core。
- **核心对象**：AgentRun、PendingAction、Application、JobEvent、ToolResult。
- **前置依赖**：Step 4.1–Step 4.4。
- **完成结果**：形成可重复的 fake QQ + fake LLM E2E 测试矩阵；任一未确认路径都证明 Application 和 JobEvent 未变化。

### 5. 本阶段产物

- `UpdateApplicationStatus` Tool Definition、Input/Output JSON Schema、权限配置。
- Write Proposal 到 PendingAction 的转换器和展示摘要 Renderer。
- QQ 确认/拒绝 Command Router 集成。
- AgentRun 等待确认、恢复、成功、失败和重试逻辑。
- ToolResult 记录和最终回复路径。
- 写操作幂等、权限边界、Prompt Injection、重启恢复 E2E 测试。

### 6. 验收标准

#### A. 功能验收

- **Given** Application“腾讯/后端/校招”当前为 `INTERVIEW round=2`，**When** 用户发送“腾讯已经三面了”，**Then** LLM 只能产生 `INTERVIEW round=3` Proposal，系统创建 `PENDING` PendingAction 并回复确认码，Application 和 JobEvent 不变。
- **Given** 上述 PendingAction，**When** 用户发送正确确认码，**Then** PendingAction 按 `CONFIRMED → EXECUTING → COMPLETED` 变化，Application round 变为 3，新增一条 JobEvent 和一条 OutboxEvent，AgentRun 收到 ToolResult 并发送最终回复。
- **Given** PendingAction，**When** 用户发送拒绝，**Then** PendingAction 为 `REJECTED`，AgentRun 结束或回到可继续对话状态，但 Application、JobEvent、Outbox 不变。
- **Given** 用户输入存在多个 PendingAction 且只发送“确认”，**When** Router 处理，**Then** 不执行任何 Action，并要求用户提供确认码。

#### B. 数据验收

- `resolvedArguments`、`displaySummary`、`proposalFingerprint` 在创建后不可变。
- PendingAction 与 AgentRun、ToolCall、User、sourceType/sourceId 的关联可追溯。
- 成功写操作产生最多一条 `UNIQUE(pendingActionId)` JobEvent；失败不留下半条 JobEvent。
- ToolResult 明确记录 `pendingActionId` 和受影响 Application ID。

#### C. 幂等验收

- 同一确认码连续提交两次，只有第一次能领取执行；最多一条 JobEvent，第二次返回已处理状态。
- 用户重复发送同一自然语言请求但第一条 PendingAction 尚未确认时，不自动创建无界重复 Action；相同指纹应复用或明确提示已有 Action。
- AgentRun 恢复时不会重新让 LLM 生成写参数，也不会重复执行已完成 PendingAction。

#### D. 重启恢复验收

- PendingAction 为 `PENDING` 时进程退出，重启后仍可通过确认码确认。
- PendingAction 为 `CONFIRMED` 或 `EXECUTING` 时进程退出，重启后可继续执行或明确转为 `FAILED` 并可重试，不重复 JobEvent。
- AgentRun 为 `WAITING_USER_CONFIRMATION` 时重启，Session 仍能显示待确认信息，确认后恢复原 Run 或给出明确不可恢复提示。

#### E. 权限 / Human-in-the-loop 验收

- 未确认 Proposal 时，Application、JobEvent、Outbox 和任何状态查询结果保持旧值。
- LLM 返回“已确认”“用户同意”或邮件中包含类似文字，不能替代真实 QQ 确认命令。
- 确认后执行参数必须来自已冻结 PendingAction；用户后续消息不能隐式修改目标状态。
- LLM 不能调用 SQL、Repository 或未注册的 Write Tool。

#### F. 自动化测试要求

- **Unit Test**：Write Tool Schema、权限闸门、Proposal 指纹、确认码解析、PendingAction 状态转换、结果摘要。
- **Integration Test**：确认执行事务、重复领取、失败重试、AgentRun 状态恢复、ToolResult 持久化。
- **End-to-End Test**：QQ 自然语言 → Proposal/确认提示 → 确认 → Application/JobEvent/Outbox → 最终回复；另测拒绝、并发和注入。

### Demo Scenario

1. 预置腾讯 Application 为 `INTERVIEW round=2`。
2. 通过 QQ fake gateway 发送“腾讯已经三面了”。
3. 检查 AgentRun 为 `WAITING_USER_CONFIRMATION`，PendingAction 为 `PENDING`，Application 仍为 round 2。
4. 发送“确认 PA-XXXX”，检查 Application 变为 round 3、JobEvent 和 OutboxEvent 各增加一条。
5. 重复发送同一确认命令，检查不增加第二条 JobEvent。
6. 再创建一个 PendingAction，只发送“确认”，检查系统要求确认码且两个 Action 都未执行。

### Definition of Done

- [x] UpdateApplicationStatus Write Tool、Schema、权限和业务校验完成。
- [x] Proposal → PendingAction → Confirmation → Execution 链路完成。
- [x] AgentRun 挂起、确认后恢复、ToolResult 和最终回复完成。
- [x] 多 PendingAction、确认码、重复确认、失败重试和重启恢复完成。
- [x] 核心 Unit / Integration / End-to-End 测试通过。
- [x] 未确认写操作绝不修改 Application 或 JobEvent。
- [x] 没有提前实现 AddKnowledge / RemoveKnowledge 或并行 Tool Calling。
- [x] Tool Schema、Prompt 边界、安全说明和操作文档已同步更新。

### 阶段 4 验收结果

阶段 4 已于 2026-09-10 验收通过，继续使用
`0005_conversation_agent (head)`，无需新增 migration。自动化验收共 54 个测试，
覆盖 QQ HTTP Webhook 到自然语言写提议、确定性确认、冻结参数执行、原
AgentRun 恢复、ToolResult 和最终 QQ push 的完整闭环。恢复测试还覆盖了持久化
ToolCall 后重启不重复调用 LLM、确认提示投递失败后的生命周期重试，以及终态
PendingAction 与等待中 AgentRun 的幂等对账；确认提示与提议在同一事务中持久化。

质量门禁：`pytest`、Ruff lint/format、basedpyright、`alembic check` 和
`alembic current` 全部通过；Ruff format 检查覆盖 94 个文件，basedpyright 为
0 errors / 0 warnings / 0 notes。详细结果见
[`docs/phase-4-verification.md`](phase-4-verification.md)。

## Phase 5 - RAG

### 1. 阶段目标

建立 `QQ File → UserFile → ParseDocument → PendingAction → KnowledgeDocument/Chunk → Embedding → Chroma → SearchKnowledge` 闭环。关系型数据库先保存知识事实，Chroma 只作为异步、可重建的向量索引；文档生命周期和索引状态必须分离。

完成后，用户可以通过 QQ 上传 JD 或面经，确认入库，等待索引完成，并通过 SearchKnowledge 检索；删除时先从关系型检索范围移除，再异步清理 Chroma。

### 2. 前置依赖

#### 代码依赖

- Phase 0 的文件目录、配置、异步任务和测试 fake。
- Phase 3 的 QQ Webhook 文件事件接收、Session 和 Agent Runtime。
- Phase 4 的 PendingAction、确认 Router 和 Agent 写操作模式。

#### 数据模型依赖

- `User`、`Session`、`PendingAction`、`OutboxEvent`。
- `ToolRegistry` 和 AgentRun。

#### 外部服务依赖

- QQ 文件事件和文件下载能力，或 fake file provider。
- PDF/DOCX/Markdown/TXT 解析器。
- Embedding Service，或 fake embedding。
- 本地 Chroma，或 fake Chroma。

### 3. 本阶段范围

#### 做什么

- 支持 Markdown、TXT、PDF、DOCX，限制文件大小和类型，保存原始 UserFile。
- 实现 ParseDocument Transform Tool，输出待确认的结构化文档信息。
- 实现 AddKnowledge / RemoveKnowledge Write Tool 和 PendingAction。
- 实现 KnowledgeDocument、KnowledgeChunk、生命周期状态和索引状态。
- 实现确定性 chunking、Embedding、一个 Chroma Collection、metadata filter 和 chunk ID Upsert/Delete。
- 实现 RAG Index Worker、失败重试、READY/FAILED 状态和 SearchKnowledge Retrieval Tool。
- 将 RAG Tool 接入 Agent，使状态、邮件和知识可以在一个 AgentRun 中顺序查询。

#### 暂时不做什么

- 不把 Conversation Transcript 自动写入知识库。
- 不把 Chroma 当作唯一知识事实源，不在 Chroma 中保存无法从关系库重建的业务状态。
- 不引入多 Collection、分布式向量服务、通用文件解析平台或自动知识分类 Agent。
- 不允许 LLM 未确认直接增加或删除知识。

### 4. 实施步骤

#### Step 5.1：实现 QQ 文件接收、UserFile 保存和边界校验

- **目标**：将合法 QQ 文件事件保存为可重新解析的原始文件，并阻止超限或不支持的文件进入解析。
- **涉及模块**：QQ Gateway.receive、UserFile、文件存储、上传校验。
- **核心对象**：`UserFile`、文件类型、大小、原始路径/内容、provider file ID。
- **前置依赖**：Phase 3 Webhook、Phase 4 PendingAction 基础。
- **完成结果**：文件事件快速返回并持久化；支持 Markdown/TXT/PDF/DOCX；超限、未知类型和下载失败有明确状态，不调用解析/Embedding。

#### Step 5.2：创建 KnowledgeDocument、KnowledgeChunk 和状态 schema

- **目标**：建立关系型知识事实、生命周期和索引状态。
- **涉及模块**：Knowledge persistence、migration、Repository。
- **核心对象**：`KnowledgeDocument`、`KnowledgeChunk`、`lifecycleStatus`、`indexStatus`、索引尝试字段。
- **前置依赖**：Step 5.1；可与 Step 5.3 并行设计。
- **完成结果**：Document 与 Chunk 可查询、过滤、重建；满足 `UNIQUE(documentId, chunkIndex)`；知识事实不依赖 Chroma 是否可用。

#### Step 5.3：实现 ParseDocument Tool 和结构化 AddKnowledge Proposal

- **目标**：把原始文件解析成可审阅的标题、类型、正文、标签和关联字段，但不产生知识事实。
- **涉及模块**：Document Parser、ParseDocument Tool、LLM/规则辅助元数据提取、Tool Schema。
- **核心对象**：ParsedDocument 结果、documentType、company/department/position、knowledgeDomain、tags。
- **前置依赖**：Step 5.1；Step 5.2 的 Schema 定义。
- **完成结果**：解析在事务外和后台/线程池执行；结果经过 Schema 校验；AddKnowledge Proposal 显示文件、文档类型、摘要和将要入库的字段；未确认时不创建 KnowledgeDocument。

#### Step 5.4：实现确认后的 AddKnowledge 事务与切片

- **目标**：确认后把知识事实和切片一次性保存，并产生 `KNOWLEDGE_DOCUMENT_ADDED`。
- **涉及模块**：Knowledge Management、Chunking、PendingAction Executor、Outbox。
- **核心对象**：`KnowledgeDocument`、`KnowledgeChunk[]`、`KNOWLEDGE_DOCUMENT_ADDED`。
- **前置依赖**：Step 5.2、Step 5.3、Phase 4 的确认执行链路。
- **完成结果**：一个事务写入文档、切片、chunkCount、初始 `indexStatus=PENDING` 和 Outbox；`KNOWLEDGE_DOCUMENT_ADDED` 表示关系库已保存，不表示 Chroma 已 READY；Chunk ID 稳定可重用。

#### Step 5.5：实现 Embedding、Chroma Collection 和 RAG Index Worker

- **目标**：在数据库事务外为 ACTIVE 文档建立可重建向量索引。
- **涉及模块**：Embedding Service、Chroma adapter、RAG Index Worker。
- **核心对象**：一个 Chroma Collection、`KnowledgeChunk.id`、Chroma metadata、`indexStatus`。
- **前置依赖**：Step 5.4。
- **完成结果**：Worker 将文档置为 `INDEXING`，生成 embedding 并 Upsert；成功置 `READY` 并记录模型/版本/时间；失败保留关系库内容并置 `FAILED`，可按持久化状态重试；所有外部调用在事务外。

#### Step 5.6：实现 SearchKnowledge、RemoveKnowledge 和索引清理

- **目标**：提供只读检索和需要确认的删除操作，严格按关系库生命周期过滤。
- **涉及模块**：Knowledge Retrieval、SearchKnowledge Tool、RemoveKnowledge Tool、Chroma Delete Worker。
- **核心对象**：`SearchKnowledgeInput/Output`、Chroma metadata filter、`lifecycleStatus`、`indexStatus`。
- **前置依赖**：Step 5.5。
- **完成结果**：SearchKnowledge 先按 user/document filters 查询 READY 文档的 Chroma 结果并返回 chunk/document 引用；删除先确认并将关系文档设为 REMOVED，再异步删除对应 chunk ID；删除后不能被检索。

#### Step 5.7：接入 Agent 复合 RAG 查询并完成 E2E

- **目标**：在同一个 AgentRun 中按顺序调用 Application Tool、Mail Tool 和 SearchKnowledge，生成基于真实数据的回答。
- **涉及模块**：Conversation Agent、RAG Tools、Application/Mail Tools、Prompt/Answer Renderer。
- **核心对象**：ToolCall sequence、ToolResult.contextRefs、知识检索结果。
- **前置依赖**：Step 5.6、Phase 3、Phase 4。
- **完成结果**：用户可以询问状态、最近邮件并结合 JD/面经获得准备建议；最终回答区分 Application、Mail 和 RAG 来源，不把 RAG 结果写回业务状态。

### 5. 本阶段产物

- UserFile 文件接收、存储、类型/大小校验和状态。
- ParseDocument Tool、解析器适配和 ParsedDocument Schema。
- KnowledgeDocument、KnowledgeChunk migration、Repository、状态字段和索引尝试字段。
- AddKnowledge / RemoveKnowledge PendingAction 链路。
- Chunker、Embedding Service、Chroma adapter、metadata Schema 和单 Collection。
- RAG Index Worker、SearchKnowledge Retrieval Tool、删除清理 Worker。
- RAG 与复合 Agent 查询 E2E 测试、重建说明和文件格式文档。

### 6. 验收标准

#### A. 功能验收

- **Given** 用户通过 QQ 上传一份合法 PDF JD，**When** 文件被接收并解析，**Then** UserFile 保存，ParseDocument 返回结构化预览并创建 AddKnowledge PendingAction；未确认时 KnowledgeDocument 数量为 0。
- **Given** 用户确认 AddKnowledge，**When** 执行完成，**Then** 创建一条 ACTIVE KnowledgeDocument、至少一个 KnowledgeChunk，`indexStatus=PENDING`，并产生一条 `KNOWLEDGE_DOCUMENT_ADDED` OutboxEvent。
- **Given** RAG Index Worker 成功运行，**When** 调用 SearchKnowledge 查询 JD 中的关键词，**Then** 只返回该用户 ACTIVE 且 READY 文档的 chunk，并包含 chunkId、documentId、title、content、score 和 metadata。
- **Given** 用户确认 RemoveKnowledge，**When** 删除开始，**Then** 关系库先变为 `lifecycleStatus=REMOVED`，之后 Chroma 删除对应 chunk IDs；删除完成前后都不能再被 SearchKnowledge 返回。
- **Given** 用户提出“腾讯现在到哪一步，最近邮件说了什么，结合我的 JD 和面经告诉我应该准备什么”，**When** Agent 运行，**Then** 同一 AgentRun 顺序调用 Application Tool、Mail Tool、SearchKnowledge，并生成最终回答。

#### B. 数据验收

- `KnowledgeChunk` 满足 `UNIQUE(documentId, chunkIndex)`，不保存向量。
- Chroma Record ID 与 `KnowledgeChunk.id` 完全一致；metadata 包含 userId、documentId、documentType、关联字段、sectionTitle、chunkIndex、embeddingVersion。
- KnowledgeDocument 的 `lifecycleStatus` 和 `indexStatus` 独立保存：ACTIVE/REMOVED 与 PENDING/INDEXING/READY/FAILED。
- 关系库在 Chroma 失败时仍保留 KnowledgeDocument/Chunk 和错误信息；Chroma READY 不反向决定关系库是否存在。
- `KNOWLEDGE_DOCUMENT_ADDED` 只表示关系库文档和切片已保存，不得把它解释为向量索引已经完成。

#### C. 幂等验收

- 同一个 AddKnowledge PendingAction 重复确认或恢复执行，最多创建一份对应文档和一组 chunk。
- 同一文档相同 chunk ID 重复 Chroma Upsert，不新增重复 Record。
- Index Worker 在 Upsert 后退出，重启重试不会创建重复 chunk；数据库状态最终为 READY 或明确 FAILED。
- RemoveKnowledge 重复执行不会把已 REMOVED 文档重新加入检索，也不会因重复 Chroma Delete 产生错误业务事实。

#### D. 重启恢复验收

- 文档已保存但 Index Worker 尚未运行，重启后 `PENDING` 文档会被扫描并索引。
- 文档为 `INDEXING` 时进程退出，重启后 stale 状态会恢复为可重试状态，不永久卡住。
- Embedding 或 Chroma 失败后，文档保留在关系库并进入 `FAILED`，重新启动或手动重试可再次执行。
- Chroma 数据目录损坏或被删除时，可根据 KnowledgeDocument/Chunk 从关系库重建，且不需要重新上传文件。

#### E. 权限 / Human-in-the-loop 验收

- ParseDocument 只能生成预览和 AddKnowledge Proposal，不能直接创建 KnowledgeDocument 或 KnowledgeChunk。
- 未确认 AddKnowledge 时，关系库知识数据和 Chroma 均不发生写入。
- RemoveKnowledge 必须经过 PendingAction；LLM 或文件内容不能隐式删除知识。
- 知识正文中的指令不能改变 System Prompt、Tool Registry 或用户权限。

#### F. 自动化测试要求

- **Unit Test**：文件校验、Parser 输出 Schema、chunking、metadata、SearchKnowledge filters、生命周期/索引状态机。
- **Integration Test**：文档/Chunk 事务、PendingAction 幂等、fake Embedding/Chroma Upsert/Delete、READY/FAILED 恢复。
- **End-to-End Test**：QQ file → Parse → confirmation → DB document/chunks → index → search；另测删除、重启和三类 Tool 复合查询。

### Demo Scenario

1. 通过 QQ fake gateway 上传一份腾讯 JD 和一份面经 Markdown。
2. 确认两份文件都生成解析预览和 AddKnowledge PendingAction，未确认前数据库没有知识文档。
3. 分别确认，观察 KnowledgeDocument、KnowledgeChunk 和 `indexStatus=PENDING`。
4. 运行 RAG Index Worker，确认 Chroma 中 Record ID 等于 Chunk ID，数据库变为 READY。
5. 通过 QQ 提问“结合腾讯 JD 和面经，二面应该准备什么”，确认 SearchKnowledge 返回结果并生成回答。
6. 确认删除其中一份面经，确认关系库先 REMOVED，随后检索结果不再包含它。

### Definition of Done

- [ ] QQ 文件接收、UserFile 保存、类型/大小边界和解析失败状态完成。
- [x] ParseDocument、AddKnowledge、RemoveKnowledge 的 Schema、Proposal 和确认链路完成。
- [x] KnowledgeDocument、KnowledgeChunk、生命周期/索引状态和关系库 Source of Truth 完成。
- [x] Chunking、Embedding、Chroma Upsert/Delete、Index Worker 和 SearchKnowledge 完成。
- [ ] 核心 Unit / Integration / End-to-End 测试通过。
- [ ] 文件上传、确认、索引、检索和删除主流程可以实际运行。
- [x] 没有引入通用文件平台、多 Collection 或关系库之外的知识事实源。
- [ ] Chroma 重建、文件格式、Embedding 配置和 RAG 使用文档已同步更新。

### 验收结果（2026-09-11）

当前结论为 **部分通过**。核心关系库/向量闭环、迁移和代码质量门禁通过，完整
E2E 与重建演练仍待完成。

- `uv run pytest -q`：58 passed。
- `uv run ruff check .`：通过。
- `uv run ruff format --check .`：通过，114 files already formatted。
- `uv run basedpyright`：0 errors、0 warnings、0 notes。
- Python source-policy audit：31 个本阶段涉及文件无违规。
- Alembic：`0006_phase5_rag -> 0005_conversation_agent -> 0006_phase5_rag`
  往返成功。
- LocalChroma：临时目录 Upsert、Query、Delete smoke test 通过，返回 Record ID
  与 `KnowledgeChunk.id` 契约一致。

已验证上传写盘不占用数据库事务、事务失败清理孤儿文件、确认后创建关系知识事实、
索引成功进入 READY、Embedding 失败进入 FAILED、SearchKnowledge 检索以及 REMOVED
后立即不可检索并异步清理 Chroma。

尚未满足的完整验收项：QQ PDF 经 HTTP/Agent 完整入库 E2E、Application/Mail/RAG
三类 Tool 的单 AgentRun 复合查询、stale INDEXING 重启恢复测试，以及 Chroma 丢失后的
完整重建命令和演练。详细证据见
[`phase-5-verification.md`](phase-5-verification.md)。

## Phase 6 - Reliability & Operations

### 1. 阶段目标

将前五个阶段的可运行闭环提升为可部署、可恢复、可维护的 v1。重点不是增加新业务功能，而是补齐 Outbox、Notification、AgentRun、PendingAction、RAG Index 的持久化重试、stale 恢复、失败可见性、SQLite 备份和 Chroma 重建。

完成后，进程在关键副作用执行中退出不会永久丢失任务；任务要么继续处理，要么进入带错误原因和重试入口的明确 FAILED 状态。个人用户可以通过文档化命令完成启动、备份、恢复、检查和重建。

### 2. 前置依赖

#### 代码依赖

- Phase 1 的 Outbox 和 PendingAction。
- Phase 2 的 Outbox Publisher、Notification、Dispatcher。
- Phase 3/4 的 AgentRun 恢复和写操作。
- Phase 5 的 RAG Index Worker、KnowledgeDocument/Chunk 和 Chroma adapter。

#### 数据模型依赖

- OutboxEvent、ProcessedEvent、Notification、NotificationAttempt、AgentRun、PendingAction、KnowledgeDocument、KnowledgeChunk。

#### 外部服务依赖

- 可选真实 IMAP/QQ/LLM/Embedding/Chroma 环境用于部署 smoke test。
- 不要求新增基础设施服务。

### 3. 本阶段范围

#### 做什么

- 完善数据库驱动的 Outbox Publisher、Notification Dispatcher、AgentRun 恢复和 RAG Index Worker。
- 统一 attemptCount、nextAttemptAt、lastError、stale lease/startedAt 和退避策略。
- 增加失败任务查询、重试、取消/过期和健康检查入口。
- 增加 SQLite 备份、完整性检查、恢复演练和 Chroma 从关系库重建命令。
- 增加优雅关闭、启动恢复扫描、故障注入测试和最终全链路验收。
- 编写单进程部署、systemd 或等价进程管理、文件权限和凭证管理文档。

#### 暂时不做什么

- 不引入分布式任务队列、Redis、消息中间件、容器编排或多副本一致性方案。
- 不改变业务领域模型、阶段顺序、Tool Calling 顺序和 PendingAction 确认协议。
- 不承诺 Exactly-once、零重复通知或企业级灾备。
- 不把人工运维命令变成复杂 Web 管理平台。

### 4. 实施步骤

#### Step 6.1：完善 Outbox Publisher 和事件消费者恢复

- **目标**：让所有 Outbox 事件可扫描、领取、投递、重试，并通过 ProcessedEvent 保持消费者幂等。
- **涉及模块**：Outbox Publisher、EventBus、ProcessedEvent、所有事件消费者。
- **核心对象**：Outbox 状态、attemptCount、nextAttemptAt、lastError、`ProcessedEvent` 唯一约束。
- **前置依赖**：Phase 1/2。
- **完成结果**：Publisher 可在进程重启后继续；事件重复投递不会重复创建 Notification/PendingAction；失败事件达到策略上限后进入明确 FAILED 或可人工重试状态。

#### Step 6.2：完善 Notification Dispatcher 和 stale SENDING 恢复

- **目标**：使主动 QQ 通知尽量不丢，并把外部发送不确定性记录为 NotificationAttempt。
- **涉及模块**：Notification、NotificationAttempt、Dispatcher、QQ Gateway.push。
- **核心对象**：PENDING/SENDING/RETRY_WAIT/SENT/FAILED、attemptNo、providerMessageId、stale 时间。
- **前置依赖**：Phase 2。
- **完成结果**：发送前持久化 attempt；超时/异常进入可重试状态；长时间 SENDING 在启动或扫描时恢复；超过最大次数后 FAILED 且可查询/手动重试；允许极端重复发送但不影响业务幂等。

#### Step 6.3：完善 PendingAction 和 AgentRun 启动恢复

- **目标**：处理确认、执行、Agent Loop 在进程退出后的中间状态。
- **涉及模块**：PendingAction Executor、Agent Runtime、Session lock/queue、启动恢复扫描。
- **核心对象**：PendingAction `CONFIRMED/EXECUTING/FAILED`、AgentRun `RUNNING/WAITING_USER_CONFIRMATION/FAILED`。
- **前置依赖**：Phase 4。
- **完成结果**：确认码仍可安全使用；Executing Action 可重试而不重复业务结果；超过 2 分钟的 AgentRun 进入 FAILED 或重新调度；用户得到明确失败说明。

#### Step 6.4：实现 RAG 失败重试、删除清理和全量重建

- **目标**：保证 Chroma 丢失或不一致时，关系库仍可作为唯一恢复依据。
- **涉及模块**：RAG Index Worker、Chroma adapter、rebuild command、Knowledge Repository。
- **核心对象**：KnowledgeDocument indexStatus、KnowledgeChunk、Chroma Collection、embeddingVersion。
- **前置依赖**：Phase 5。
- **完成结果**：可重试 FAILED/INDEXING 文档；可清理 REMOVED 文档；可删除并重新创建 Collection，再从 ACTIVE 文档和 Chunk 全量 Upsert；重建后检索结果与关系库状态一致。

#### Step 6.5：实现失败任务查询、健康检查和备份恢复

- **目标**：让个人用户能知道哪里失败、执行一次重试或恢复，而不需要直接修改数据库。
- **涉及模块**：Operations CLI/health endpoint、备份脚本、恢复检查。
- **核心对象**：失败 Outbox、Notification、AgentRun、PendingAction、KnowledgeDocument 索引状态。
- **前置依赖**：Step 6.1–Step 6.4。
- **完成结果**：可查询失败记录、最近错误、attemptCount、nextAttemptAt 和关联业务 ID；SQLite 备份后可在临时目录恢复并通过 integrity check；Chroma 重建命令有明确输出和退出码。

#### Step 6.6：完成部署、故障注入和 v1 全量验收

- **目标**：证明单进程部署和关键退出点下系统可恢复。
- **涉及模块**：进程 supervisor、启动/关闭、所有 Worker、E2E test suite、运维文档。
- **核心对象**：Outbox、Notification、AgentRun、PendingAction、KnowledgeDocument/Chunk。
- **前置依赖**：Step 6.1–Step 6.5。
- **完成结果**：新机器可按文档部署；故障注入、重启、重复输入和最终十个业务场景全部通过；没有“永久 RUNNING”“无记录失败”或无法解释的半状态。

### 5. 本阶段产物

- Outbox Publisher、ProcessedEvent 幂等、重试和失败状态。
- Notification Dispatcher、stale SENDING 恢复、Attempt 记录和手动重试入口。
- AgentRun/PendingAction 启动恢复、超时和失败可见性。
- RAG Index 重试、删除清理和 Chroma 全量重建命令。
- 失败任务查询、健康检查、SQLite 备份/恢复/完整性检查命令。
- 单进程部署配置、systemd 或等价 supervisor 示例、权限和凭证文档。
- 故障注入、重启恢复、重复输入和完整 v1 E2E 测试报告。

### 6. 验收标准

#### A. 功能验收

- **Given** Outbox、Notification、RAG Index 中分别存在可执行记录，**When** Worker 正常扫描，**Then** 每类记录都能推进到 PUBLISHED/SENT/READY 或明确 FAILED，并保留关联业务 ID。
- **Given** 一个失败 Notification，**When** 运维执行受控重试，**Then** attemptCount 增加、旧错误保留、下一次状态可观察，成功后变为 SENT。
- **Given** Chroma Collection 被删除，**When** 执行 rebuild 命令，**Then** ACTIVE 文档的 READY 索引被重新生成，REMOVED 文档不被写入，SearchKnowledge 恢复可用。
- **Given** SQLite 备份文件，**When** 恢复到临时目录并执行 integrity check，**Then** 迁移版本、核心表、唯一约束和可查询业务数据一致。

#### B. 数据验收

- Outbox 记录保留 attemptCount、nextAttemptAt、lastError、publishedAt 和状态。
- ProcessedEvent 满足 `UNIQUE(consumerName, eventId)`，重复事件不会重复消费结果。
- Notification/Attempt 状态和 attemptNo、时间、providerMessageId 一致；SENDING stale 有明确恢复规则。
- AgentRun、PendingAction、KnowledgeDocument 的失败状态都有错误原因和最后尝试时间。
- 数据库备份恢复后，Application、JobEvent、Mail、PendingAction、Notification、KnowledgeDocument/Chunk 的引用关系仍有效。

#### C. 幂等验收

- 重复发布 Outbox 不重复生成消费者结果。
- 重复 Notification retry 允许最多出现外部 QQ 重复发送，但不重复 Application/JobEvent/KnowledgeDocument。
- 重复 AgentRun 恢复不会重复 ToolResult 或 Write Tool 业务结果。
- 重复 Chroma rebuild/upsert 不产生重复 Record，Record ID 始终等于 Chunk ID。

#### D. 重启恢复验收

- 进程在 Outbox 发布前、发布后消费者处理前、Notification 外部调用中、RAG Chroma Upsert 中分别退出；重启后任务继续或进入明确 FAILED，不永久丢失。
- stale `SENDING`、`INDEXING`、`EXECUTING`、`RUNNING` 均能在启动扫描中被识别和处理。
- 恢复过程不在数据库事务中等待外部服务；外部失败只更新持久化任务状态。

#### E. 权限 / Human-in-the-loop 验收

- 运维重试命令只能重试已有任务，不能跳过 PendingAction 创建或确认而创建新的 Application、JobEvent 或知识事实。
- 失败任务查看不会泄漏完整邮件、简历、凭证或 LLM Key；日志和诊断输出只显示必要摘要和 ID。
- 备份、重建和失败查询命令不能改变业务状态，除文档化的重试/恢复动作外不得提供任意 SQL 写入口。

#### F. 自动化测试要求

- **Unit Test**：退避、stale 判断、任务领取、状态恢复、备份路径校验和失败分类。
- **Integration Test**：真实 SQLite Worker 扫描/领取/重试、ProcessedEvent 唯一性、NotificationAttempt、Chroma fake 重建。
- **End-to-End Test**：十个最终场景、故障注入退出点、备份恢复、完整部署 smoke test。

### Demo Scenario

1. 预先创建一个待发布 Outbox、一个待发送 Notification、一个 `INDEXING` KnowledgeDocument 和一个运行中的 AgentRun。
2. 在各 Worker 外部调用前终止进程。
3. 重启服务，观察 Outbox、Notification、RAG Index、AgentRun 均被扫描。
4. 让 fake QQ/Chroma/Embedding 在第一次调用失败，确认记录进入 RETRY_WAIT/FAILED 并有错误信息。
5. 执行重试或恢复命令，确认 Outbox 发布、Notification 发送、RAG READY 和 AgentRun 明确结束。
6. 删除 Chroma Collection，执行 rebuild，确认 SearchKnowledge 恢复且 REMOVED 文档未回流。
7. 执行 SQLite 备份和临时目录恢复检查。

### Definition of Done

- [x] Outbox、Notification、AgentRun、PendingAction、RAG Index 的持久化重试和 stale 恢复完成。
- [x] ProcessedEvent、NotificationAttempt、错误字段和任务状态查询完成。
- [x] SQLite 备份/恢复/integrity check 完成；注入式/fake Chroma rebuild 完成。
- [x] 生产 Bailian Embedding + local Chroma rebuild capability is wired with fake-injectable CLI coverage; temporary-path LocalChroma rebuild and search pass with deterministic `FakeEmbedding` and isolated SQLite.
- [x] 单进程部署、优雅关闭、启动恢复和 Worker 调度文档完成。
- [x] Credential-free recovery, duplicate-input, backup, and lifecycle tests pass。
- [x] Real Bailian network smoke completed with `text-embedding-v4` returning 1024 finite values; the key was loaded from ignored `.env`, no key value or production indexing path was recorded。
- [x] Credential-free v1 lifecycle shutdown and recovery paths are covered by tests; isolated CLI rebuild and retrieval also passed without using production data。
- [x] 没有引入架构文档明确排除的基础设施或并行 Agent 编排。
- [x] 运维、配置、安全、备份和恢复文档已同步更新。

### 阶段 6 验收结果（2026-09-11）

自动化验收结果：完整测试套件 120 tests passed（加入真实 LocalChroma 集成测试和 CLI
资源清理回归测试前为 118 tests）；Ruff、格式检查、basedpyright 和 Alembic 检查结果见
[`phase-6-verification.md`](phase-6-verification.md)。真实 LocalChroma 在临时路径上使用确定性
`FakeEmbedding` 和隔离 SQLite 通过 `rebuild_index` 与 search。边界覆盖包括空白 Embedding
Key 归一化、无 Key 时即使配置 Chroma 路径也不启用生产索引、Key 无路径仍然无效、provider
error code 限制为 64 字符 ASCII classification grammar、抑制敏感 transport 和 malformed-response
traceback cause、配对 factory ownership，以及 cleanup 失败时保留 Chroma 构造错误。真实 Bailian
网络 smoke 已执行并返回 1024 个有限值；隔离 CLI rebuild 汇总为
`documents=1 ready=1 failed=0 chunks=1 upserted=1 partial_failure=False`，retrieval 返回一个
匹配文档且 score 有效。API key 从 ignored `.env` 加载，临时脚本和数据已删除，未记录密钥值、
输入或查询文本、文档内容、向量、原始 provider response、生产路径或其他敏感值。

## Phase Dependency Map

### 主阶段依赖

```text
Phase 0 - Project Bootstrap
        ↓
Phase 1 - Application Core
        ↓
Phase 2 - Mail Pipeline
        ↓
Phase 3 - QQ Conversation Agent
        ↓
Phase 4 - Agent Write Operations
        ↓
Phase 5 - RAG
        ↓
Phase 6 - Reliability & Operations
```

### 阶段内主要依赖

```text
Phase 0:
0.1 → 0.2 → 0.3 → 0.4 → 0.5

Phase 1:
1.1 → 1.2 ─┐
           ├→ 1.3 ─┐
1.4 → 1.5 ─┘       ├→ 1.6 → 1.7

Phase 2:
2.1 → 2.2 → 2.3 → 2.4 ─┐
                         ├→ 2.5 → 2.6 → 2.7
                         ┘

Phase 3:
3.1 → 3.2 ─┐
3.3 ───────┼→ 3.4 → 3.5 → 3.6
           ┘

Phase 4:
4.1 → 4.2 → 4.3 → 4.4 → 4.5

Phase 5:
5.1 ─┐
     ├→ 5.3 → 5.4 → 5.5 → 5.6 → 5.7
5.2 ─┘

Phase 6:
6.1 ─┐
6.2 ─┼→ 6.5 → 6.6
6.3 ─┤
6.4 ─┘
```

并行仅限于不共享未完成写目标的工作；任何涉及同一 migration、同一状态机或同一事务边界的 Step，必须按上图顺序集成和验收。

## Full v1 Acceptance Checklist

以下清单是从整个系统角度的最终验收，不替代各 Phase 的局部验收。所有项目同时通过后，才可将系统称为完整可部署 v1。

### Scenario 1：人工创建和更新求职状态

- [ ] **Given** 空数据库，**When** 通过本地命令或受控 Write Tool 提交并确认“腾讯/后端/校招 = APPLIED”，**Then** 创建唯一 Application、首条 JobEvent（`previousStatus=null`）和对应 OutboxEvent。
- [ ] **When** 再确认 `APPLIED → INTERVIEW round=1`，**Then** Application 更新且新增一条 JobEvent。
- [ ] **When** 重复确认同一 PendingAction，**Then** JobEvent 数量不增加。
- [ ] **When** 只修改普通展示信息或重复相同状态/轮次，**Then** 不产生 JobEvent。

### Scenario 2：普通求职邮件闭环

- [ ] **Given** IMAP 有一封普通求职通知邮件，**When** Poller 执行，**Then** 完成 `IMAP → Mail → Analysis → Notification → QQ`。
- [ ] 数据库中只有一条 Mail、一条当前 JobMailAnalysis、一条普通 Notification；没有 PendingAction、JobEvent 或 Application 状态变化。
- [ ] Notification 经过持久化后才调用 QQ push，发送结果可在 NotificationAttempt 查询。

### Scenario 3：状态变化邮件确认闭环

- [ ] **Given** IMAP 有一封状态变化邮件，**When** 完成分析，**Then** 完成 `Mail → Analysis → PendingAction → QQ Confirmation`。
- [ ] 用户未确认前，Application、JobEvent 和 `APPLICATION_STATUS_CHANGED` 均不变化。
- [ ] **When** 用户确认，**Then** 完成 `PendingAction → Application Update`，并在同一事务中写 Application、JobEvent、OutboxEvent 和 PendingAction 完成状态。
- [ ] 状态变化邮件不同时生成普通邮件 Notification。

### Scenario 4：QQ 查询真实 Application

- [ ] **Given** 数据库中有腾讯 Application，**When** 用户通过 QQ 问“腾讯现在什么状态？”，**Then** Agent 调用 Application Read Tool 并基于真实结果回答。
- [ ] 回答不以 Session Summary、LLM 记忆或 RAG 内容覆盖数据库当前状态。
- [ ] 重复 QQ event 不产生第二条 ConversationMessage 或 AgentRun。

### Scenario 5：QQ 查询最近邮件

- [ ] **Given** 数据库中有多封腾讯邮件，**When** 用户问“腾讯最近一封邮件说了什么？”，**Then** Agent 调用 Mail Tool，按 receivedAt 返回正确邮件并可继续读取分析/正文。
- [ ] 邮件内容来自 Mail/JobMailAnalysis，不能被 Application 状态或 LLM 猜测替代。
- [ ] 查询过程只读，不产生 PendingAction、JobEvent 或 Notification。

### Scenario 6：QQ 自然语言写操作确认

- [ ] **Given** Application 当前为 `INTERVIEW round=2`，**When** 用户说“腾讯已经三面了”，**Then** 只创建 `UpdateApplicationStatus` Proposal 和 PendingAction，AgentRun 进入 `WAITING_USER_CONFIRMATION`。
- [ ] 用户未确认时 Application、JobEvent、Outbox 不变化。
- [ ] **When** 用户确认正确短码，**Then** 执行冻结参数，Application 为 `INTERVIEW round=3`，新增一条 JobEvent 和一条状态变化 Outbox。
- [ ] **When** 用户拒绝或确认不存在短码，**Then** 不产生业务写入。

### Scenario 7：文件入库和 RAG 索引

- [ ] **Given** 用户上传 JD/面经，**When** 执行 `File → ParseDocument → PendingAction`，**Then** 文件被保存、解析结果可审阅，未确认前没有 KnowledgeDocument。
- [ ] **When** 用户确认 AddKnowledge，**Then** 创建 KnowledgeDocument/Chunk，关系库为 Source of Truth，`indexStatus=PENDING`，产生 `KNOWLEDGE_DOCUMENT_ADDED`。
- [ ] **When** Index Worker 成功，**Then** Chunk 以自身 ID Upsert 到 Chroma，文档变为 READY，SearchKnowledge 可以检索。
- [ ] 重复确认、重复 Upsert 不产生重复业务事实或重复 Chunk。

### Scenario 8：状态、邮件和 RAG 复合问题

- [ ] **Given** 腾讯 Application、最近邮件、JD 和面经都已存在且知识索引 READY，**When** 用户提出复合问题，**Then** 同一个 AgentRun 顺序调用 Application Tool、Mail Tool、RAG Tool。
- [ ] ToolCall sequence、ToolResult 和 contextRefs 均持久化。
- [ ] 最终回答区分 Application 当前事实、Mail 原文/分析和 RAG 建议，不把 RAG 建议写成业务状态。
- [ ] Tool 调用不超过 8 次，AgentRun 不超过 2 分钟。

### Scenario 9：进程退出与恢复

- [ ] **Given** 存在未发布 Outbox、未发送 Notification、未完成 RAG Index 和可恢复 AgentRun，**When** 进程在各外部调用关键点退出并重新启动，**Then** 任务不会永久丢失。
- [ ] Outbox 最终为 PUBLISHED 或明确 FAILED；Notification 最终为 SENT、RETRY_WAIT 或 FAILED；KnowledgeDocument 最终为 READY 或 FAILED；AgentRun 最终为 COMPLETED 或 FAILED。
- [ ] stale SENDING、INDEXING、EXECUTING、RUNNING 均有恢复策略。
- [ ] 恢复过程不在数据库事务内等待外部服务。

### Scenario 10：重复输入与至少一次语义

- [ ] 同一邮件重复输入不产生重复 Mail 或重复 `MAIL_RECEIVED`。
- [ ] 同一 Domain Event 重复投递不产生重复消费者结果。
- [ ] 同一 PendingAction 重复确认不产生重复 JobEvent。
- [ ] 同一 KnowledgeChunk 重复 Chroma Upsert 不产生重复 Record。
- [ ] Notification 极端情况下允许重复发送，但重复发送不重复修改 Application、JobEvent 或知识事实。
- [ ] 所有上述行为都有自动化测试，而不是只依赖人工观察。

### 系统级安全与边界

- [ ] 邮箱密码、QQ 凭证、LLM Key 和 Embedding Key 不在 Git 中，配置文件权限受限。
- [ ] QQ Webhook 具备签名或 Token 校验，非法发送者不能创建消息、AgentRun 或文件。
- [ ] 上传文件有类型和大小限制，PDF/DOCX 解析不阻塞主事件循环。
- [ ] 日志不打印完整简历、完整邮件、凭证和 Key。
- [ ] 邮件、RAG、文件和 LLM 输出无法改变系统指令、Tool 权限或 PendingAction 规则。
- [ ] LLM 没有数据库写权限；所有 Write Tool 都经过 PendingAction 和确定性确认。

### v1 发布判定

- [ ] Phase 0–6 的 Definition of Done 全部完成。
- [ ] Full v1 Acceptance Checklist 的 Scenario 1–10 全部通过。
- [ ] SQLite 备份、恢复、完整性检查和 Chroma 重建至少各执行一次成功演练。
- [ ] 新机器按部署文档可以启动单进程，并在不连接真实外部服务时通过 smoke test。
- [ ] 连接真实 IMAP、QQ、LLM、Embedding 和本地 Chroma 后，至少完成一次端到端演示。
- [ ] 所有失败任务都有持久化状态、错误原因和可重复处理方式。
- [ ] 计划外的功能没有被混入 v1；没有引入架构文档明确排除的基础设施。
