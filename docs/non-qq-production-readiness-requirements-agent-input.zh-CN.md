# 除 QQ 线上验证外的生产就绪需求，需求处理 Agent 输入

## 1. 文档目的

本文把当前审计中除 QQ 服务器部署和真实 QQ provider 验证之外的生产阻断项、功能缺口、运行控制和证据缺口整理为可执行需求。本文供下游需求处理 Agent 使用，需求 ID、状态和验收含义必须保持稳定。

本文不声称项目已经生产就绪。关闭本文所有可执行需求，也不等于 QQ 线上能力已经验收。

## 2. 范围边界

### 2.1 纳入范围

- 知识 `AddKnowledge`、`RemoveKnowledge` 的本地事件路由正确性。
- QQ webhook ACK 时序、passive/proactive 回复目标的本地确定性代码契约。它们只能通过 fake transport、fake gateway、临时 SQLite 和本地测试验收。
- 测试环境隔离、数据库迁移目标一致性、健康和 durable task 运行语义。
- 当前审计证据与历史记录的对账。
- 非 QQ IMAP 端到端证据、非 QQ LLM 和复合 `AgentRun` 端到端证据。
- SQLite、WAL 和应用数据、uploads、Chroma 重建输入的完整恢复演练，以及隔离恢复、RPO、RTO 和清理。
- 可复现部署和 supervisor 产物、CI 质量和证据产物、发布版本追溯、回滚流程、运行手册和监控。

### 2.2 明确不纳入可执行范围

本文不创建、安排或暗示以下可执行任务：QQ 服务器部署、公网 webhook、真实 token 或 API endpoint、URL challenge、真实 C2C 收发、真实主动推送、真实附件或文件或图片能力、真实 provider 兼容性、真实线上重启恢复，以及需要人工批准的 QQ smoke。

本文中的本地 QQ 测试只证明代码契约。**本地确定性测试不得被表述为 live QQ 验证，也不得被表述为 provider 兼容性证明。**

本文不包含安全、隐私、授权、权限、secret、资源上限、路径、保留期限、漏洞或其他安全治理需求。QQ 排除表中仅记录为何无法在当前文档执行验证。

## 3. 术语、状态和优先级

### 3.1 状态

下游 Agent 必须使用以下状态值，不得自行改名：

| 状态 | 含义 |
|---|---|
| `Confirmed Defect` | 当前源码或测试已经直接证明存在行为错误。 |
| `Required Control` | 生产运行必须具备的控制或明确语义，当前不一定对应单一实现缺陷。 |
| `Operational Gap` | 部署、恢复、发布或日常运行所需产物或流程尚不完整。 |
| `Evidence Required` | 可能已有部分实现，但当前证据不足以作出发布判断。 |
| `Open Decision` | 需要在实现前确定业务或运维选择，本文只规定决策输出，不替代决策。 |
| `QQ-Excluded/Blocked` | 依赖 QQ 部署、真实 provider、凭据、人工批准或线上环境，登记但不创建执行任务。 |

### 3.2 优先级

| 优先级 | 含义 |
|---|---|
| `P0` | 阻止生产发布，或会造成业务事实错误、不可恢复运行故障或关键链路不可用。 |
| `P1` | 生产前应完成的高风险控制、运维产物或证据。没有批准的风险例外不得带缺口发布。 |
| `P2` | 不阻止首个受控发布，但必须有负责人、截止日期和后续版本门禁。 |

### 3.3 每项需求的固定字段

每个可执行需求都必须保留以下字段：标题、类别、状态、优先级、当前发现、理由、必须结果、TDD 或 test-first 切片、验收标准、依赖、证据或参考、验证步骤、发布影响。下游 Agent 可以拆分实现任务，但不得改变这些字段的含义。

## 4. 当前证据基线

审计基线日期为 2026-09-13，基于当前工作树。历史阶段数字不能替换本节基线。

| 事实 | 当前结果和解释 |
|---|---|
| 受控全套测试 | 当前工作树在明确禁用 IMAP 和 LLM 的受控配置下为 `404 passed`。这只证明本地确定性测试在该受控配置下通过。 |
| 修复前默认工作目录测试 | 修复前默认 `.env` 存在时为 `373 passed + 2 failed`。失败是 readiness fixture 受到工作目录 `.env` 的 IMAP 和 LLM 启用值影响，不能称为全套通过。 |
| 迁移检查 | `uv run alembic check` 通过，无新的 upgrade operation。 |
| 当前 schema head | `0008_qq_reply_targets (head)`。 |
| 本地运行、健康和 SIGTERM | 已有本地 run、health、正常关闭和 SIGTERM 通过记录。该记录不等于线上 QQ 重启恢复。 |
| LLM adapter 单元测试 | `uv run pytest -q tests/unit/test_openai_compatible_llm.py` 为 `31 passed`。只证明 adapter contract、MockTransport 和解析路径，不证明完整 provider 兼容性。 |
| 既有质量记录 | Ruff、format、basedpyright、`uv lock --check`、`git diff --check` 和改动 Python 文件诊断已有通过记录。发布时必须绑定到待发布 commit 或 artifact 重跑。 |
| 数据库改动 | 本轮已完成数据库目标一致性相关实现和本地证据记录，但没有新增 migration；schema head 仍为 `0008_qq_reply_targets (head)`。该结果尚未绑定发布 artifact 或 CI。 |

### 4.1 主要证据

- [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md)
- [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)
- [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md)
- [docs/phase-5-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-5-verification.md)
- [docs/phase-6-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-6-verification.md)
- [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md)
- [证据索引](evidence/index.md)
- [需求证据矩阵](evidence/requirements-matrix.md)
- [当前证据基线](evidence/current-baseline.md)
- [最终本地门禁记录](evidence/final-gate-2026-09-13.md)

### 4.2 当前处理状态对账

下表只对账本地实现和证据进度，不改变各需求的原始状态，也不关闭发布门禁。详细命令、结果和限制见[证据索引](evidence/index.md)、[需求证据矩阵](evidence/requirements-matrix.md)、[当前证据基线](evidence/current-baseline.md)和[最终本地门禁记录](evidence/final-gate-2026-09-13.md)。

| 需求 | 原始状态 | 已记录的本地进度 | 仍需满足的门禁 |
|---|---|---|---|
| `REQ-F01` | `Confirmed Defect` | 知识和邮件 PendingAction 路由、重放及回滚测试已记录。 | 本地非 QQ 范围内无新增实现项；QQ 通知投递仍排除。 |
| `REQ-F02` | `Confirmed Defect` | receipt 先于 ACK、handler 不阻塞 ACK、持久化失败和关闭测试已记录。 | 不据此推导 live QQ webhook 或 provider 重投行为。 |
| `REQ-F03` | `Confirmed Defect` | passive/proactive target 校验、持久化、重启重建和 fake gateway 测试已记录。 | 不据此推导 live QQ 收发。 |
| `REQ-CFG01` | `Confirmed Defect` | ambient `.env` 隔离和当前受控 `404 passed` 已记录。 | 必须在最终 artifact 对应 CI 中重跑。 |
| `REQ-CFG02` | `Operational Gap` | 迁移命令和应用 health 使用同一临时数据库，未生成旁路数据库。 | 必须绑定部署 artifact 和 CI 记录。 |
| `REQ-CFG03` | `Required Control` | `/live`、`/health`、schema、capability、失败任务和陈旧任务的本地 HTTP 矩阵已记录。 | supervisor 目标和监控行为仍需选定并演练。 |
| `REQ-E01` | `Evidence Required` | 当前基线、历史计数、索引和矩阵已对账。 | 发布 artifact 身份和留存 CI 证据仍未完成。 |
| `REQ-E02` | `Evidence Required` | credential-free fake IMAP 业务 E2E 已记录。 | 真实 provider 业务路径兼容性仍为 `Evidence Required`。 |
| `REQ-E03` | `Evidence Required` | fake composite AgentRun E2E 和重启重放已记录。 | 真实 provider composite E2E 仍为 `Evidence Required`。 |
| `REQ-OPS01` | `Operational Gap` | 本地 fixture 级隔离恢复、WAL、upload、关系计数、LocalChroma 重建和清理已记录。 | 数值 RPO/RTO 批准和完整运维演练仍未完成。 |
| `REQ-OPS02` | `Operational Gap` | runtime 和 deployment contract 已记录。 | supervisor 目标、版本化 artifact、异常重启演练和 checksum 仍未完成。 |
| `REQ-OPS03` | `Operational Gap` | CI 质量和证据 contract 已记录，本地 gate 通过。 | CI provider、留存运行产物和阻断发布的 pipeline 仍未完成。 |
| `REQ-OPS04` | `Operational Gap` | release manifest contract 和本地校验测试已记录。 | 生成 artifact、checksum、source/build 绑定和干净安装仍未完成。 |
| `REQ-OPS05` | `Operational Gap` | rollback 流程和停止条件已记录。 | 两版本 artifact 回滚演练仍未完成。 |
| `REQ-OPS06` | `Operational Gap` | monitoring/runbook 响应矩阵已记录。 | 监控平台、告警投递和独立操作者演练仍未完成。 |
| `QQ-EX-01` 至 `QQ-EX-06` | `QQ-Excluded/Blocked` | 仅登记排除边界，没有执行 live QQ 验证。 | 继续由独立、经批准的 human-gated QQ 流程处理，不得标记为 `PASS`。 |

## 5. 可执行需求

### REQ-F01 知识 PendingAction 的 Outbox 路由

- **标题**：区分知识动作与邮件状态动作的 `PENDING_ACTION_CREATED` 路由。
- **类别**：功能正确性、事件处理和通知。
- **状态**：`Confirmed Defect`。
- **优先级**：`P0`。
- **当前发现**： [event_consumers.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/event_consumers.py) 的 `consume_action_created` 无条件要求 `PendingAction.mail_analysis_id`，随后查找 `JobMailAnalysis` 和 `Mail`。 [knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py) 创建 `AddKnowledge` 和 `RemoveKnowledge` 时没有 `mail_analysis_id`。因此知识动作发布的 `PENDING_ACTION_CREATED` 可能误入邮件确认通知路径并失败。
- **理由**：知识确认是独立业务链路。将没有邮件分析的知识动作当作邮件状态动作处理，会使确认通知无法完成，且可能反复重试同一错误。
- **必须结果**：按 `action_type`、`source_type` 或等价的明确聚合信息分流邮件状态提案和知识提案。知识动作不得依赖 `mail_analysis_id`，邮件状态动作继续使用邮件分析和原始邮件关联。重复事件消费保持幂等，并产生可查询的处理结果。
- **TDD 或 test-first 切片**：先为 `AddKnowledge` 和 `RemoveKnowledge` 各写一个失败测试，断言生成 outbox 事件后不读取 `mail_analysis_id`；再写邮件状态动作、重复投递和事务失败测试；最后实现最小路由修改并运行相关 suite。
- **验收标准**：
  1. `AddKnowledge` 和 `RemoveKnowledge` 产生事件后，可以创建正确的知识确认通知，不查找 `mail_analysis_id`。
  2. 邮件分析产生的状态提案仍能关联原始邮件并生成原有确认通知。
  3. 聚合类型缺失或不匹配时，事件进入明确的可诊断失败状态，不生成半条通知。
  4. 同一事件重复消费不会重复通知、重复知识事实或重复 `ProcessedEvent`。
  5. 回归测试覆盖两类知识动作、邮件动作、重复投递和失败回滚。
- **依赖**：`PendingAction`、知识 proposal、Outbox、Notification、`ProcessedEvent` 现有模型和事务边界。不依赖真实 QQ。
- **证据或参考**： [event_consumers.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/event_consumers.py:135)、[knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py:37)、[tests/integration/test_knowledge_pipeline.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_knowledge_pipeline.py)、[docs/phase-5-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-5-verification.md:20)。
- **验证步骤**：使用临时 SQLite 创建 AddKnowledge 和 RemoveKnowledge；检查 Outbox、PendingAction、Notification 和 ProcessedEvent 行；重复消费同一事件；注入通知事务异常；记录命令、测试数量、schema head 和结果。
- **发布影响**：关闭前禁止生产发布，属于 P0 发布门禁。该项是本地功能缺陷，与 QQ 线上验证无关。

### REQ-F02 QQ webhook ACK 的本地时序契约

- **标题**：持久化回执先于普通 ACK，长任务不进入 ACK 前路径。
- **类别**：本地 QQ 代码契约、事件接收和生命周期。
- **状态**：`Confirmed Defect`。
- **优先级**：`P0`。
- **当前发现**： [qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py) 的 `StarletteEventTransport._dispatch_request` 在收到 accepted durable receipt 后，仍在 `489-492` 行 `await handler(parse_gateway_event(payload))`，完成 handler 后才在 `495` 行返回普通 ACK。只要 handler 包含耗时工作，ACK 就会被其阻塞；当前没有覆盖该时序的测试证据。
- **理由**：ACK 早于 durable receipt 会使重复投递和丢失事件难以解释。LLM、工具、文件、Embedding、Chroma 等长任务不应决定普通 ACK 的返回时刻。
- **必须结果**：普通 dispatch 只有在本地 durable receipt 明确接受后才能成功响应。持久化失败必须返回非成功响应。收到 receipt 后的长任务或 SDK handler 失败不得改写已发出的持久化接受结果。
- **TDD 或 test-first 切片**：先用 fake request、可控事件和事件闸门写时序失败测试；再写长任务阻塞测试、持久化异常测试和重复 dispatch 测试；最后实现或调整 transport 与 intake 的边界。
- **验收标准**：
  1. 测试可观察到 durable receipt 完成先于成功 ACK。
  2. 注入未完成的长任务时，成功 ACK 不等待长任务完成。
  3. 注入持久化异常时，不返回成功 ACK，且结果属于明确的本地错误分类。
  4. 重复 dispatch 不新增消息、`AgentRun`、文件或业务事实。
  5. 所有测试使用 fake transport、fake gateway 和临时 SQLite，不要求真实 QQ。
- **依赖**：`DurableIngestionReceipt`、webhook intake、事件幂等记录、Starlette transport 和生命周期测试 seam。
- **证据或参考**： [qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py:386)、[qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py:456)、[tests/integration/test_qq_webhook.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_qq_webhook.py:70)、[docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md:18)。
- **验证步骤**：运行本地 transport 集成测试；记录 receipt、handler、ACK 的事件时间顺序；检查数据库计数；执行一次长任务和一次持久化失败注入；明确记录“local deterministic contract only”。
- **发布影响**：本地契约未关闭前，P0 发布门禁关闭。本文不要求或接受真实 QQ webhook 收发作为本项验收；真实 QQ 验证仍由 QQ 排除项处理。

### REQ-F03 passive 与 proactive ReplyTarget 本地契约

- **标题**：被动回复和主动推送保持明确且不可混淆的目标模式。
- **类别**：本地 QQ 代码契约、回复目标重建和投递分流。
- **状态**：`Confirmed Defect`。
- **优先级**：`P0`。
- **当前发现**： [agent/contracts.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/agent/contracts.py) 要求 passive target 有 `message_id` 和 `event_id`。但 [qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py) 的 `BotpyQQGateway.reply()` 在 `176-185` 行直接丢弃 `user_id` 和 `message_id`，然后构造 `ReplyMode.PROACTIVE` target，导致该兼容 API 无法保持被动回复语义。
- **理由**：模式错误可能导致回复目标无法重建、被动回复降级为主动推送，或重放时产生不同业务副作用。
- **必须结果**：从已持久化入站事件重建 passive target 时保留 provider、scope、target、message ID、event ID 和可用序号；`reply()` 不得静默丢弃被动上下文并降级为 proactive。proactive target 只包含主动目标字段。缺字段和不可重放条件必须有确定性本地结果。
- **TDD 或 test-first 切片**：先写 passive 完整字段、缺 `message_id`、缺 `event_id` 和 proactive 携带 passive 字段的失败测试；再写重启重建、`reply`、`deliver`、`push` fake gateway 分流测试；最后实现最小修正。
- **验收标准**：
  1. 从持久化入站事件重建的 target 为 passive，且保留必要字段；`reply()` 不丢弃必要的被动字段。
  2. passive 缺 `message_id` 或 `event_id` 时在本地边界失败，不降级为 proactive。
  3. proactive 携带 `message_id`、`event_id` 或 `msg_seq` 时被拒绝。
  4. 重放同一本地事件不会改变目标模式或生成重复业务事实。
  5. fake gateway 测试覆盖 reply、deliver、push，以及序号存在和缺失的合法输入。
- **依赖**：`ReplyTarget`、入站事件持久化、`load_context`、`ReplyMode`、fake gateway 和 schema `0008_qq_reply_targets`。
- **证据或参考**： [agent/contracts.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/agent/contracts.py:44)、[tests/unit/test_qq_reply_contracts.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_qq_reply_contracts.py:15)、[tests/integration/test_qq_reply_target_persistence.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_qq_reply_target_persistence.py:13)、[tests/integration/test_conversation_agent.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_conversation_agent.py:218)。
- **验证步骤**：运行 agent、QQ adapter 和 conversation integration tests；保存 target 字段断言、fake gateway 调用记录、重启前后数据库状态；证据标题必须写明“本地确定性契约”，不得写成 live QQ 验收。
- **发布影响**：本地契约未关闭前，P0 发布门禁关闭。真实 QQ C2C 收发仍属于本文外的 QQ 排除项。

### REQ-CFG01 测试与工作目录 `.env` 隔离

- **标题**：测试默认不受 ambient 工作目录 `.env` 影响。
- **类别**：配置和测试可重复性。
- **状态**：`Confirmed Defect`。
- **优先级**：`P0`。
- **当前发现**： [settings.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/config/settings.py) 默认设置了 `.env` 文件来源。 [tests/conftest.py](/Users/panafish/workstation/jobs_status_managerment/tests/conftest.py) 提供显式临时设置，但默认全套测试在工作目录 `.env` 启用 IMAP 和 LLM 时为 `373 passed + 2 failed`，readiness fixture 受到外部工作目录状态影响。
- **理由**：发布证据必须可重复。开发者本地配置不能改变测试预期，也不能让测试触碰真实外部能力或真实路径。
- **必须结果**：测试入口、fixture 和 CI 使用明确的设置来源。默认测试不读取未声明的工作目录 `.env` 或其他 ambient 状态。需要环境变量的测试必须显式注入，并在测试结束后恢复。
- **TDD 或 test-first 切片**：先添加带启用值 `.env`、无 `.env`、显式 disabled 环境的三组失败测试；再固定 settings 来源或测试入口的隔离方式；最后运行完整测试矩阵。
- **验收标准**：
  1. 工作目录存在启用 IMAP 和 LLM 值的 `.env` 时，默认全套测试为 `375 passed`。
  2. 无 `.env`、带 `.env`、带外部环境变量时，readiness fixture 结果一致。
  3. 测试不会读取真实外部 endpoint、真实凭据或真实持久化路径。
  4. CI 使用干净工作目录或显式 disabled 配置，并在证据中记录采用方式。
- **依赖**：settings source precedence、测试 fixture 和 CI 工作目录约定。不得修改真实 `.env` 文件。
- **证据或参考**： [settings.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/config/settings.py:22)、[tests/conftest.py](/Users/panafish/workstation/jobs_status_managerment/tests/conftest.py:16)、[tests/e2e/test_health.py](/Users/panafish/workstation/jobs_status_managerment/tests/e2e/test_health.py:17)、[tests/unit/test_lifecycle.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_lifecycle.py:14)、[README.md](/Users/panafish/workstation/jobs_status_managerment/README.md:88)。
- **验证步骤**：分别在带 ambient `.env`、无 `.env` 和显式 disabled 的隔离目录运行 `uv run pytest -q`；记录三次结果、测试环境和退出码；目标结果为每次 `375 passed`。
- **发布影响**：未关闭前不得把测试绿灯作为发布证据，P0 发布门禁关闭。

### REQ-CFG02 Alembic 与应用数据库目标一致

- **标题**：所有支持的迁移检查和应用 health 检查同一数据库。
- **类别**：数据库配置和发布一致性。
- **状态**：`Operational Gap`。
- **优先级**：`P0`。
- **当前发现**： [alembic.ini](/Users/panafish/workstation/jobs_status_managerment/alembic.ini) 的静态 URL 为 `sqlite:///./jobs_status_alembic.db`，应用通过 `APP_DATABASE_PATH` 使用另一数据库。 [migrations.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/database/migrations.py) 的程序化 runner 会覆盖 URL，但直接执行 Alembic 命令可能检查错误文件。
- **理由**：迁移命令和应用如果指向不同数据库，`check` 通过不能证明实际应用库已迁移，发布结果会失去可解释性。
- **必须结果**：`alembic check`、`current`、`upgrade` 和应用 health 使用同一明确数据库目标，或命令显式要求传入同一 URL。不得静默创建旁路数据库。
- **TDD 或 test-first 切片**：先写临时数据库测试，断言迁移、`current`、health 和 `schema_migrations` 的 revision 相同；再写仓库根目录旁路文件不产生的测试；最后固定命令和文档入口。
- **验收标准**：
  1. 在临时应用数据库执行迁移和 `alembic check`，revision 与应用 health 一致。
  2. 命令不会在仓库根目录创建 `jobs_status_alembic.db` 旁路文件。
  3. 数据库路径来源、相对路径解析和 CI 参数在部署文档中清楚记录。
- **依赖**：`APP_DATABASE_PATH`、`migration_config`、`migrations/env.py`、CI 命令入口和 REQ-CFG01 的隔离原则。
- **证据或参考**： [alembic.ini](/Users/panafish/workstation/jobs_status_managerment/alembic.ini:1)、[migrations/env.py](/Users/panafish/workstation/jobs_status_managerment/migrations/env.py:29)、[migrations.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/database/migrations.py:12)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:50)。
- **验证步骤**：在隔离临时目录运行 `migrate`、`uv run alembic check`、`uv run alembic current` 和 health；比较 `schema_migrations`、health 和命令输出；检查仓库根目录未出现旁路数据库。
- **发布影响**：未关闭前禁止执行生产 schema 发布，P0 发布门禁关闭。

### REQ-CFG03 健康、存活、就绪和 durable task 语义

- **标题**：定义并验证 liveness、readiness 和 durable task 三种运行语义。
- **类别**：运行契约、健康检查和可观测性。
- **状态**：`Required Control`。
- **优先级**：`P0`。
- **当前发现**： [health.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/application/health.py) 已返回数据库、schema、readiness、durable task、失败任务和陈旧任务字段。现有文档主要称 `/health` 为 readiness，尚未形成独立的 liveness、readiness、durable task 处置矩阵。当前本地 run、health、SIGTERM 记录通过，但不能据此推导完整生产控制。
- **理由**：supervisor 和监控需要区分进程是否响应、服务是否可接收工作、持久化任务是否需要恢复或处理。混淆这些状态会导致错误接流量或错误重启。
- **必须结果**：liveness 只表示进程可响应；readiness 表示数据库和 schema 可用且服务可接收工作；durable task 状态表示任务可继续、可重试、已失败或陈旧。每种状态都有 HTTP 结果、supervisor 行为和运维处置说明。
- **TDD 或 test-first 切片**：先建立状态矩阵并为数据库不可用、schema 非 head、任务失败、任务陈旧、外部能力 disabled 和可用场景写 HTTP driver 测试；再补 liveness surface 或文档契约；最后绑定 supervisor 行为。
- **验收标准**：
  1. 文档和测试明确每项检查、HTTP 状态、失败条件和 supervisor 反应。
  2. 数据库不可用、schema 未就绪、任务失败、任务陈旧和外部能力未配置时结果可预测。
  3. liveness 不因短暂外部依赖失败而误报进程死亡；readiness 不在 schema 未就绪时返回成功。
  4. health 响应字段集合与状态矩阵一致，不返回未约定的运行细节。
  5. 不把当前 `test_health.py` 的通过记录扩大解释为线上 provider 验证。
- **依赖**：health payload、lifecycle、migration head、durable task 查询、REQ-CFG02、REQ-OPS01。
- **证据或参考**： [health.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/application/health.py:31)、[tests/e2e/test_health.py](/Users/panafish/workstation/jobs_status_managerment/tests/e2e/test_health.py:17)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:72)、[docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md:55)。
- **验证步骤**：扩展 health 测试；使用本地 HTTP driver 逐项记录状态码和 JSON 字段；运行一次 run、health、SIGTERM 和重启流程；把结果与 supervisor 和监控规则逐项对照。
- **发布影响**：未形成可执行语义前，禁止接入生产 supervisor 或以 health 作为流量切换依据，P0 发布门禁关闭。

### REQ-E01 当前证据与历史计数对账

- **标题**：建立当前工作树的唯一发布证据基线。
- **类别**：证据治理和发布判断。
- **状态**：`Evidence Required`。
- **优先级**：`P1`。
- **当前发现**： [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md) 和历史阶段文档保留了 `120 tests`、`337 passed`、`58 passed` 等快照；修复前默认 `.env` 结果为 `373 passed + 2 failed`，当前受控工作树为 `404 passed`，当前 head 是 `0008_qq_reply_targets`。历史数字如不带时间、范围和环境，会替代当前事实。
- **理由**：发布审批必须知道结果来自哪个 commit、数据库 head、配置和测试范围。旧阶段的绿色结果不能证明当前工作树通过。
- **必须结果**：每条验证记录标明日期、commit 或 artifact、环境、命令、范围、结果和限制。当前文档和发布索引使用受控 `404 passed`、修复前默认失败说明、Alembic check 通过和当前 head；历史数字保留时必须明确不可替代当前基线。
- **TDD 或 test-first 切片**：先写证据表字段和当前基线断言；再审阅 README、phase 文档、adapter 记录和发布索引；最后生成一份与当前 commit 对应的验证摘要。
- **验收标准**：
  1. 当前证据明确记录 `375 passed` 受控结果和 `373 passed + 2 failed` 默认 `.env` 结果及失败原因。
  2. 记录 `uv run alembic check` 通过和 `0008_qq_reply_targets (head)`。
  3. 历史 `120`、`337`、`58` 等数字带有日期、阶段、范围和不可替代当前基线说明。
  4. 证据索引区分本地确定性测试、有限 adapter smoke 和未执行的 QQ 线上验证。
- **当前对账说明**：上述验收标准中的 `375 passed` 是原始计数目标，按要求保留；当前扩展后的受控 suite 实际结果为 `404 passed`，见第 4.2 节和当前证据基线。
- **依赖**：REQ-CFG01、当前 CI 运行结果、发布 artifact 元数据和证据保存约定。
- **证据或参考**： [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md:52)、[docs/phase-5-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-5-verification.md:39)、[docs/phase-6-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-6-verification.md:5)、[docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md:51)。
- **验证步骤**：逐项审阅历史文档和当前文档；在待发布 commit 上重跑受控测试、Alembic check、current、health 和 adapter unit；把命令、退出码、数量、head 和限制写入证据索引。
- **发布影响**：对账完成前，P1 证据门禁保持阻塞，不能以历史绿灯替代当前证据。

### REQ-E02 非 QQ 部署范围内的 IMAP 端到端证据

- **标题**：补齐不依赖 QQ webhook 和服务器部署的 IMAP 端到端路径证据，或明确记录未验收。
- **类别**：外部适配器证据和功能验收。
- **状态**：`Evidence Required`。
- **优先级**：`P1`。
- **当前发现**：已有 QQ IMAP adapter 的有限真实 smoke，覆盖登录、poll 和 cleanup；已有 cursor、MIME、UID 和错误分类测试。该证据没有贯穿邮件持久化、分析触发、结果路由和恢复的完整 IMAP 业务路径，也不能证明超出已执行路径的 provider 兼容性。
- **理由**：adapter 单元测试和有限 smoke 只能证明指定路径。生产判断还需要一条不依赖 QQ webhook 或服务器部署的完整 IMAP E2E，或明确保留 provider 兼容性缺口。
- **必须结果**：定义一个不依赖 QQ webhook 和服务器部署的 IMAP E2E 场景，贯穿 poll、邮件持久化、分析触发、结果路由和清理。发布证据必须明确试验使用的 provider 和支持边界，不把有限 smoke 写成通用兼容性证明。
- **TDD 或 test-first 切片**：先用可控 IMAP 测试服务或项目既有 adapter seam 写完整业务路径的失败 E2E；再覆盖 cursor 延续、重复 poll、MIME、分析失败和清理；最后执行经批准的 provider 证据或标记未完成。
- **验收标准**：
  1. 至少有一条不依赖 QQ webhook 和服务器部署的完整 IMAP E2E，结果关联 commit、环境和 provider 试验范围。
  2. 测试覆盖首次 poll、后续 cursor、重复消息、邮件分析结果和失败恢复。
  3. 证据明确区分 fake、可控测试服务和真实 provider；不能将有限 IMAP smoke 写成完整 provider compatibility。
  4. 若真实 provider E2E 不在当前发布窗口，需求保持 `Evidence Required`，并记录负责人、下一步和发布限制。
- **依赖**：IMAP adapter、mail pipeline、LLM 选择、REQ-CFG01 和证据对账。不得依赖 QQ 服务器部署。
- **证据或参考**： [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md:14)、[tests/unit/test_qq_imap_cursor.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_qq_imap_cursor.py)、[tests/unit/test_qq_imap_mime.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_qq_imap_mime.py)、[tests/integration/test_mail_pipeline.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_mail_pipeline.py)。
- **验证步骤**：运行不依赖 QQ webhook 和服务器部署的 IMAP E2E；记录 poll 数量、cursor、持久化状态、分析结果、失败分类和清理结果；将真实 provider 结果与本地 fake 结果分开保存。
- **发布影响**：没有明确的完整 IMAP E2E 或批准的范围例外前，不得宣称 IMAP provider 兼容性已验证。P1 证据门禁保持关闭。

### REQ-E03 非 QQ LLM 与复合 AgentRun 端到端证据

- **标题**：补齐非 QQ LLM 端到端和 Application、Mail、SearchKnowledge 复合 AgentRun 证据。
- **类别**：LLM adapter、AgentRun 和业务组合验收。
- **状态**：`Evidence Required`。
- **优先级**：`P1`。
- **当前发现**： [docs/phase-5-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-5-verification.md) 明确完整三工具 composite Agent scenario 仍 pending。当前 `31 passed` 的 LLM adapter 单元测试证明 strict parsing、tool call identity 和 MockTransport 路径；一次 synthetic-input real LLM smoke 只验证有限请求和响应解析路径。
- **理由**：单一 adapter 测试或 synthetic smoke 不能证明真实业务上下文中的多轮工具调用、知识检索、状态读取和 AgentRun 终态。
- **必须结果**：提供不依赖 QQ webhook 的复合 AgentRun E2E，至少覆盖 Application、Mail、SearchKnowledge 的组合调用、工具结果回传、终态持久化和失败分类。真实 LLM provider 若未获得批准或未执行，必须保持未验收，不得用 synthetic smoke 代替。
- **TDD 或 test-first 切片**：先以 fake LLM 写完整三工具组合的失败 E2E；再覆盖真实 adapter 可替换 seam、工具结果 identity、重复运行、LLM 合同失败和 AgentRun 恢复；最后单独记录受控真实 LLM 证据或保留缺口。
- **验收标准**：
  1. 一条非 QQ webhook 的 composite AgentRun E2E 覆盖三类工具和完整状态转移。
  2. 测试检查 `tool_call_id`、工具结果、AgentRun 状态、delivery 状态和持久化重启后的可继续性。
   3. 证据表必须分为四栏或四行，并分别标明：fake LLM 只证明本地 AgentRun、工具组合、状态持久化和恢复契约；MockTransport 只证明真实 adapter 的请求构造、响应解析、错误分类和 contract；synthetic real LLM 只证明合成输入下的有限 provider 请求/响应路径；real provider 必须单独运行目标业务场景并记录 provider、环境、命令、退出码、结果和限制。任一类别未执行必须明确记录为 `NOT_RUN` 或 `BLOCKED`，不能用其他类别替代。
  4. 不把 `31 passed` 或 synthetic real LLM smoke 写成完整 LLM provider compatibility 或 production readiness。
- **依赖**：Agent runtime、mail pipeline、knowledge search、LLM adapter、REQ-F01、REQ-CFG01 和 REQ-E01。
- **证据或参考**： [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md:39)、[docs/phase-5-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-5-verification.md:85)、[tests/unit/test_openai_compatible_llm.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_openai_compatible_llm.py:214)、[tests/integration/test_conversation_agent.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_conversation_agent.py:699)。
- **验证步骤**：分别运行 fake LLM E2E、MockTransport adapter tests、批准的 synthetic real LLM smoke 和批准的 real-provider business E2E；每类单独记录 evidence class、provider/network use、command、exit code、result、schema head、cleanup 和 limitations。保存工具序列、状态行、重启前后快照和错误分类；明确记录未执行的 provider E2E 及 QQ 线上路径。
- **发布影响**：复合 AgentRun 证据缺失时，P1 证据门禁关闭，不能宣称完整 LLM 或 Agent 生产兼容性。

### REQ-OPS01 完整恢复演练、RPO 和 RTO

- **标题**：完成覆盖 SQLite、WAL、应用数据、uploads 和 Chroma 重建输入的隔离恢复演练。
- **类别**：备份、恢复和运维连续性。
- **状态**：`Operational Gap`。
- **优先级**：`P0`。
- **当前发现**： [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md) 已有 backup、restore-check、integrity-check 和 Chroma rebuild 命令说明。现有 Phase 6 记录证明过隔离恢复和临时 Chroma rebuild 路径，但尚未形成覆盖 SQLite WAL、应用数据、uploads、Chroma rebuild 输入、RPO、RTO、恢复后清理的完整 drill 记录。
- **理由**：SQLite 是知识事实源，Chroma 是可重建索引。只检查一个数据库副本，不能证明应用状态、上传文件和重建输入能够共同恢复。
- **必须结果**：定义备份对象和恢复顺序，完成一次不覆盖线上数据的隔离 restore drill。演练必须记录 SQLite 主库、WAL、应用数据、uploads、Chroma 重建所需输入、schema integrity、业务计数、恢复耗时、目标 RPO、目标 RTO 和临时数据清理。
- **TDD 或 test-first 切片**：先写恢复清单和可验证断言；再以临时 SQLite、WAL、应用数据、uploads 和 Chroma fixture 写失败 drill；最后执行隔离恢复、重建和清理并记录结果。
- **验收标准**：
  1. 备份清单覆盖 SQLite 一致副本、WAL、应用数据、uploads 和 Chroma 可重建输入。
  2. 恢复到隔离目录，不覆盖或替换运行中的数据库。
  3. 恢复后通过 schema、integrity、业务计数、uploads 计数和 Chroma rebuild 结果核对。
  4. 明确可接受的 RPO 和 RTO 数值，记录实际结果和偏差。
  5. 演练结束后清理临时数据库、WAL、应用数据、uploads、Chroma、脚本和输出，并记录清理结果。
- **依赖**：数据库 backup API、`restore-check`、`rebuild-chroma`、单进程停止或 quiesce 流程、REQ-CFG02、REQ-OPS03、REQ-OPS04。
- **证据或参考**： [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md:36)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:63)、[docs/phase-6-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-6-verification.md:25)、[tests/integration/test_knowledge_pipeline.py](/Users/panafish/workstation/jobs_status_managerment/tests/integration/test_knowledge_pipeline.py:171)。
- **验证步骤**：创建带有代表性数据库、WAL、应用数据、uploads 和知识文档的隔离样本；执行 backup、restore-check、integrity-check、restore 和 Chroma rebuild；测量 RPO、RTO，核对计数并完成清理。
- **发布影响**：没有可重复且有记录的完整恢复路径前，禁止生产发布，P0 发布门禁关闭。

### REQ-OPS02 可复现部署和 supervisor 产物

- **标题**：交付与目标环境匹配的版本化部署和进程监督产物。
- **类别**：部署和运行。
- **状态**：`Operational Gap`。
- **优先级**：`P1`。
- **当前发现**： [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 描述了单进程启动、迁移、bootstrap、health、SIGTERM 和资源关闭顺序，但仓库没有被当前审计证明为可复现、版本化且可直接演练的 supervisor artifact。
- **理由**：只写命令不等于部署可重复。生产需要知道启动顺序、单进程约束、工作目录、环境来源、重启策略、日志位置和停止行为。
- **必须结果**：交付与目标运行环境匹配的 supervisor artifact 或等价部署产物，并绑定版本。产物定义单进程启动、迁移、bootstrap、health 检查、SIGTERM 转发、异常退出后的重启策略、日志位置和停止顺序。不得假设第二进程可共享同一 SQLite。
- **TDD 或 test-first 切片**：先在干净目录写部署演练断言；再从 artifact 安装、迁移、bootstrap、启动、health、停止和重启；最后记录失败启动、异常退出和重复启动结果。
- **验收标准**：
  1. 从干净目录按产物完成安装、迁移、bootstrap、启动、health、停止和重启。
  2. 异常退出按已记录策略恢复，且不会启动第二 listener 或第二应用进程。
  3. SIGTERM 后任务和资源按文档顺序结束，数据库 head 可核对。
  4. artifact 与版本、commit、配置模板和 runbook 可追溯。
- **依赖**：REQ-CFG02、REQ-CFG03、REQ-OPS01、REQ-OPS03 和目标环境选择。QQ 线上部署不属于本文执行范围。
- **证据或参考**： [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:1)、[README.md](/Users/panafish/workstation/jobs_status_managerment/README.md:10)、[tests/unit/test_lifecycle.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_lifecycle.py:47)。
- **验证步骤**：在隔离本地环境安装并运行 artifact；记录进程、health、SIGTERM、退出码、schema head 和重启结果；将证据绑定到 artifact checksum 和 commit。
- **发布影响**：未完成前禁止生产部署，P1 发布门禁关闭。

### REQ-OPS03 CI 质量和证据产物

- **标题**：形成固定、可追溯的 CI quality and evidence artifact。
- **类别**：持续集成和发布证据。
- **状态**：`Operational Gap`。
- **优先级**：`P1`。
- **当前发现**：当前已有 Ruff、format、basedpyright、pytest、Alembic、锁文件和 diff 检查记录，但没有在本文范围内被证明为固定 CI pipeline 和可关联 artifact 的统一产物。
- **理由**：单次本地命令输出不能替代每个待发布 commit 的重复质量证据。CI 需要明确测试环境、退出码、当前测试数量、schema head 和失败定位。
- **必须结果**：CI 在干净环境或显式 disabled 配置下运行受控测试和质量命令，并保存测试、格式、类型、迁移检查、锁文件、diff、证据索引和构建结果。任何 gate 失败都阻止可发布 artifact 生成。
- **TDD 或 test-first 切片**：先列出 gate 和输出字段并为缺失 gate 写失败检查；再实现 pipeline；最后在一个待发布 commit 上运行完整命令组并保存产物。
- **验收标准**：
  1. CI 记录受控全套测试，当前基线为 `375 passed` 或记录经对账后的新事实。
  2. `uv run alembic check` 通过，且数据库目标与应用目标一致。
  3. Ruff、format、basedpyright、`uv lock --check`、`git diff --check` 和相关回归测试均可追溯。
  4. 每个失败项关联 commit、运行时间和 artifact 版本。
  5. CI 不因缺少 QQ 线上凭据而伪造 QQ 成功，也不把 live QQ 作为本文执行 gate。
- **依赖**：REQ-CFG01、REQ-CFG02、REQ-E01、依赖锁文件、构建环境和 artifact 存储。
- **证据或参考**： [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md:51)、[README.md](/Users/panafish/workstation/jobs_status_managerment/README.md:88)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:93)。
- **验证步骤**：在干净环境执行完整 CI 命令组；保存退出码、测试数量、schema head、commit、artifact checksum 和失败摘要；确认受控配置与工作目录 `.env` 隔离。
- **发布影响**：未完成前禁止生成可发布 artifact，P1 发布门禁关闭。

### REQ-OPS04 发布 artifact、版本和来源追溯

- **标题**：每次发布具备不可混淆的版本和来源元数据。
- **类别**：发布工程和版本追溯。
- **状态**：`Operational Gap`。
- **优先级**：`P1`。
- **当前发现**：仓库文档记录了代码、SDK、schema 和历史验证片段，但没有统一要求每次发布产出包含版本、来源 commit、依赖锁、schema head 和验证索引的 artifact 清单。
- **理由**：部署、回滚和故障定位必须能从运行版本回到源码、迁移、依赖和验证结果。历史 Phase 6 head `0007_phase6_reliability` 与当前 head `0008_qq_reply_targets` 已证明版本上下文不能省略。
- **必须结果**：每个发布 artifact 包含应用版本、来源 commit、Python 和 uv 版本、SDK 版本、依赖锁摘要、schema head、构建时间、checksum 和验证报告索引。启动前可以检查版本和 schema head 是否匹配。
- **TDD 或 test-first 切片**：先为 artifact manifest 定义必需字段和缺失字段失败测试；再构建并安装 artifact；最后验证 manifest、源码 commit、lockfile、migration 目录和运行 health 一致。
- **验收标准**：
  1. 同一版本可在干净环境复现安装。
  2. artifact 元数据与源码 commit、lockfile 和 migration 目录一致。
  3. 运行前可检查版本和 head，冲突时停止部署流程。
  4. 验证索引能定位 CI 结果、恢复演练和 runbook 版本。
- **依赖**：REQ-OPS03、REQ-CFG02、REQ-OPS02、依赖锁文件和 artifact 存储。
- **证据或参考**： [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md:64)、[docs/phase-6-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-6-verification.md:5)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:48)。
- **验证步骤**：构建一次 artifact，在干净目录安装；运行 health、受控测试、`alembic check` 和 `current`；保存 manifest、checksum、commit 和结果索引。
- **发布影响**：未完成前不得标记正式版本或将构建物交给生产环境，P1 发布门禁关闭。

### REQ-OPS05 回滚流程和版本降级演练

- **标题**：建立可执行、可停止的应用和 schema 回滚流程。
- **类别**：发布操作和故障恢复。
- **状态**：`Operational Gap`。
- **优先级**：`P1`。
- **当前发现**：现有部署文档描述启动和迁移，运行文档描述失败查看、retry、backup 和 restore-check，但没有一份与 artifact、schema head、数据库备份和 supervisor 绑定的回滚流程及演练证据。
- **理由**：发生发布故障时，仅有“重新部署旧版本”不足以证明应用、数据库 schema、任务状态和索引状态可回到一致可操作状态。
- **必须结果**：提供回滚前检查、停止或 quiesce、artifact 选择、schema 处理、数据库恢复选择、health 验证、任务状态核对和恢复后的清理步骤。流程必须说明不可执行的降级组合，不得依赖临时改代码。
- **TDD 或 test-first 切片**：先用两个版本化 artifact 和隔离数据库写回滚演练断言；再执行一次应用回滚和一次恢复到备份的流程；最后记录耗时、失败点和停止条件。
- **验收标准**：
  1. runbook 能从当前 artifact 回到指定旧 artifact，并能验证版本和 schema head。
  2. schema 不兼容时流程在启动流量前停止，不继续运行未知状态。
  3. 回滚后 health、durable task、业务计数和 Chroma 状态可核对。
  4. 演练记录 artifact、commit、数据库快照、实际耗时、RPO 或 RTO 影响和清理结果。
- **依赖**：REQ-OPS01、REQ-OPS02、REQ-OPS04、REQ-OPS06 和 REQ-CFG03。
- **证据或参考**： [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:48)、[docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md:36)、[docs/phase-6-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/phase-6-verification.md:25)。
- **验证步骤**：在隔离环境安装新旧 artifact；执行停止、回滚或隔离恢复、迁移检查、health、任务检查和清理；保存逐步结果和停止条件。
- **发布影响**：未完成前禁止正式发布，P1 发布门禁关闭。

### REQ-OPS06 运行手册和监控闭环

- **标题**：形成覆盖日常操作、失败处理、恢复和监控响应的 runbook。
- **类别**：运维流程和可观测性。
- **状态**：`Operational Gap`。
- **优先级**：`P1`。
- **当前发现**： [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md) 已列出启动、失败查看、retry、backup、restore-check、integrity-check 和 rebuild-chroma 命令；[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 已描述启动、health 和 shutdown。尚缺覆盖健康状态、durable task、IMAP 或 LLM 失败、知识索引失败、恢复、回滚和监控告警的统一操作手册与演练记录。
- **理由**：值班人员需要在不改代码的前提下判断是否继续运行、暂停接收、重试、恢复或回滚。只记录命令而没有触发条件和停止条件，无法形成可执行流程。
- **必须结果**：runbook 覆盖启动失败、schema 不一致、数据库锁或不可用、任务失败或陈旧、IMAP 失败、LLM 失败、知识索引或 Chroma 重建失败、备份恢复、回滚、异常重启和监控响应。每项写明前置条件、命令或操作、预期结果、停止条件、数据影响、证据保存和升级路径。
- **TDD 或 test-first 切片**：先按故障类型编写 runbook 验收清单；再由未参与实现的操作者执行健康检查、失败定位、受控 retry、隔离恢复和回滚；最后根据记录修订手册并保留演练证据。
- **验收标准**：
  1. 操作者无需修改代码即可完成健康检查、失败定位、受控 retry、隔离恢复和回滚。
  2. runbook 与 REQ-CFG03 的状态矩阵、REQ-OPS01 的恢复流程和 REQ-OPS05 的回滚流程一致。
  3. 监控至少能发现 readiness 不可用、durable task degraded、失败任务和陈旧任务，并指向对应 runbook。
  4. 至少完成一次 restore drill 和一次异常重启 drill，记录时间、版本、结果、残留清理和后续行动。
- **依赖**：REQ-CFG03、REQ-OPS01、REQ-OPS02、REQ-OPS05、监控平台或等价本地演练环境。
- **证据或参考**： [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md:5)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md:48)、[tests/e2e/test_health.py](/Users/panafish/workstation/jobs_status_managerment/tests/e2e/test_health.py:17)。
- **验证步骤**：按 runbook 执行健康检查、失败任务检查、retry、backup、restore-check、integrity-check、rebuild-chroma、SIGTERM 和回滚演练；核对监控事件是否链接正确手册。
- **发布影响**：runbook 和监控闭环缺失时，P1 发布门禁关闭，除非有书面风险例外。

## 6. 依赖波次

### Wave 0，证据和目标隔离

完成 `REQ-CFG01`、`REQ-CFG02`、`REQ-E01` 和 `REQ-OPS03` 的基础工作。当前本地参考结果为受控 `404 passed`；最终结束条件仍包括在待发布 artifact 对应环境中重复该结果、保持 `.env` 隔离、让迁移检查和应用 health 指向同一数据库，并记录 head、命令和限制。

### Wave 1，本地功能和运行语义

完成 `REQ-F01`、`REQ-F02`、`REQ-F03` 和 `REQ-CFG03`。结束条件是知识事件路由、本地 QQ ACK、ReplyTarget 模式和健康或 durable task 语义都有 test-first 回归证据。Wave 1 不启动真实 QQ 服务，不使用真实 QQ provider 结果作为验收前提。

### Wave 2，非 QQ 证据闭环

完成 `REQ-E02` 和 `REQ-E03`，或由负责人记录明确的范围决定、未完成原因、后续日期和发布限制。有限 IMAP smoke、`31 passed` LLM adapter unit 和 synthetic real LLM smoke 不得直接升级为完整 provider compatibility 证据。

### Wave 3，恢复和部署工程

完成 `REQ-OPS01`、`REQ-OPS02`、`REQ-OPS04` 和 `REQ-OPS05`。结束条件是隔离恢复、RPO、RTO、版本化部署、artifact manifest 和回滚演练都能追溯到同一 artifact 或 commit。

### Wave 4，运行交接和发布前复核

完成 `REQ-OPS06`，重新执行所有 P0 和 P1 gate，核对测试数量、schema head、数据库目标、artifact、恢复记录、runbook 和监控。任何失败都回到对应波次处理，不得用 QQ 排除项的 `BLOCKED` 状态伪造通过。

## 7. 发布门禁

下游 Agent 只有在以下条件全部满足时，才能向发布审批提供“非 QQ 范围证据完整”的结论。该结论不是“项目生产就绪”结论。

1. `REQ-F01`、`REQ-F02`、`REQ-F03`、`REQ-CFG01`、`REQ-CFG02` 和 `REQ-CFG03` 的 P0 验收关闭。
2. `REQ-E01`、`REQ-E02`、`REQ-E03` 的当前证据、范围和限制已明确。缺失的 provider E2E 必须以 `Evidence Required` 保留，不能用有限 smoke 替代。
3. 受控全套测试在待发布 commit 或 artifact 上通过，当前工作树参考基线是 `404 passed`。修复前默认 `.env` 的 `373 passed + 2 failed` 只能作为隔离问题历史证据，不能作为发布绿灯。
4. `uv run alembic check` 通过，应用和 Alembic 检查同一数据库，当前 head 为 `0008_qq_reply_targets` 或有记录的新 head。
5. `REQ-OPS01`、`REQ-OPS02`、`REQ-OPS03`、`REQ-OPS04`、`REQ-OPS05` 和 `REQ-OPS06` 的 P1 产物或批准的风险例外齐全。
6. 每项证据包含命令、时间、commit 或 artifact、环境、结果、失败原因、清理结果和适用限制。
7. 本文明确区分本地确定性测试、受控 fake 或 MockTransport、有限真实 adapter smoke、非 QQ E2E 和 QQ 线上未执行项。
8. 任何风险例外必须包含受影响需求 ID、具体风险、范围、补偿控制、负责人、批准人、有效期、回滚条件和下一次复核日期。口头同意不构成发布授权。

## 8. QQ 非可执行排除登记表

以下项目只登记为当前文档外的阻塞项，不创建实现任务，不纳入本文发布 gate 的可执行通过条件。它们都必须标记为 `QQ-Excluded/Blocked`。本地 deterministic 测试不得替代这些项目的 live QQ 验证。

| 排除 ID | 项目 | 状态 | 为什么不在本文执行 |
|---|---|---|---|
| QQ-EX-01 | QQ 公网 webhook、服务器绑定、公开 endpoint 和网络入口 | `QQ-Excluded/Blocked` | 需要部署服务器、公网环境和人工批准。本文只验收本地 transport 和 intake 代码契约。 |
| QQ-EX-02 | URL challenge 的真实 QQ 交互 | `QQ-Excluded/Blocked` | 需要真实 QQ token、API endpoint、provider 行为和人工批准。 |
| QQ-EX-03 | 真实 C2C send、receive、passive reply 和 proactive push | `QQ-Excluded/Blocked` | 需要真实账号、收发对象、真实 provider 结果和人工批准。本文的 REQ-F02、REQ-F03 只验证本地确定性契约。 |
| QQ-EX-04 | 真实 QQ 附件下载、文件发送和图片能力 | `QQ-Excluded/Blocked` | 需要真实 provider capability、真实文件或图片路径和人工批准。 |
| QQ-EX-05 | provider ambiguous outcome 的线上行为 | `QQ-Excluded/Blocked` | 需要受控网络中断、真实 provider 返回和人工判断，不能由 fake gateway 推导。 |
| QQ-EX-06 | 在线 QQ 重启恢复和会话恢复 | `QQ-Excluded/Blocked` | 需要真实在线会话、服务器部署、provider 状态和人工 smoke。本文只验收本地 lifecycle、durable state、supervisor 和隔离重启演练。 |

排除项不能标记为 `PASS`，也不能被写成本文已经完成。它们应在另一个经批准的 QQ live 验证流程中单独处理。本文继续保留这些阻塞项，是为了防止“非 QQ 范围完成”被误读为整体生产就绪。

## 9. 给下游需求处理 Agent 的执行规则

1. 保留所有 `REQ-*` 和 `QQ-EX-*` ID，不重编号，不把多个 ID 合并成无法追踪的泛化任务。
2. 每个实现提交必须回链到需求 ID、测试或演练证据、当前 commit 和发布影响。
3. 先写失败测试、演练断言或证据字段，再实现或补文档。没有可执行断言的“完成”不能关闭需求。
4. 不得把 fake gateway、MockTransport、临时 SQLite、本地 health、有限 IMAP smoke、`31 passed` adapter unit 或 synthetic real LLM smoke 描述为真实 QQ provider 验证。
5. 不得以历史 `120 tests`、`337 passed`、`58 passed` 或旧 schema head 替代当前基线。
6. 不得修改本文范围以吸收 QQ 服务器部署、公网绑定、真实凭据、真实 provider 兼容性或人工 gated smoke。
7. 本文关闭后仍不能宣称项目生产就绪。最终结论必须由独立发布审批根据当前证据、剩余 QQ 阻塞项和批准的风险例外作出。
