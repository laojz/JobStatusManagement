# 非 QQ 生产就绪需求

## 1. 文档目的与范围

本文是生产就绪审计的需求代理输入文档。它把当前审计证据转换为可执行、可验收、可发布判断的工作项。

本文件的范围是：排除需要真实 QQ 服务器、公网入口或人工凭据的验证后，完成其余缺陷修复、控制建设、运行准备和证据补齐。本文不声称项目已经生产就绪，也不把 QQ 服务器现场验证列为本文件的可执行需求。

所有工作项必须保留本文件中的 ID。需求代理不得用历史计划中的测试数量替换当前证据，不得把“测试通过”解释为第三方服务兼容性已经验证。

## 2. 当前审计基线

审计基线日期为 2026-09-13，依据当前工作树，而不是旧阶段快照。

| 事实 | 当前证据 |
|---|---|
| 修复前默认 `uv run pytest -q` | `373 passed + 2 failed`，约 `137.61s`。两个失败由工作目录 `.env` 将 IMAP 和 LLM 的启用值泄漏到 readiness fixture，引起预期的 `disabled` 断言失效。该结果只作为修复前历史记录。 |
| 当前受控工作树 | 明确移除 ambient `APP_*` 值并禁用 IMAP 和 LLM 后，`404 passed`，退出码为 `0`。该结果尚未绑定 CI 或发布 artifact。 |
| Alembic | `uv run alembic check` 通过，无新的 upgrade operation。 |
| 当前 schema head | `0008_qq_reply_targets (head)`。 |
| 代码质量基线 | Ruff、format、basedpyright、uv lock、diff 检查均有通过记录。 |
| 已有本地边界 | IMAP cursor/MIME/UID poll、LLM strict parsing、错误分类、tool call ID 透传、资源 ownership、readiness、持久化重试、QQ webhook 本地安全边界已有确定性证据。 |
| 未完成判断 | 有限 IMAP 和 synthetic real LLM smoke 不代表完整 provider compatibility 或 production readiness。 |

支持这些事实的主要材料：

* [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md)
* [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md)
* [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)
* [docs/configuration.md](/Users/panafish/workstation/jobs_status_managerment/docs/configuration.md)
* [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md)
* [证据索引](evidence/index.md)
* [需求证据矩阵](evidence/requirements-matrix.md)
* [当前证据基线](evidence/current-baseline.md)
* [最终本地门禁记录](evidence/final-gate-2026-09-13.md)

### 2.1 当前处理状态

本文件保留审计阶段的原始需求集；其中部分同名 `REQ-OPS*` 与后续需求处理 Agent 输入中的编号含义不同，不能依据同名 ID 直接推导本文件需求已经关闭。当前执行范围的权威逐项对账见[需求处理 Agent 输入的当前处理状态对账](non-qq-production-readiness-requirements-agent-input.zh-CN.md#42-当前处理状态对账)，对应证据见[需求证据矩阵](evidence/requirements-matrix.md)。

Agent 输入范围内已经记录本地功能修复、确定性测试、fake E2E、局部恢复演练和运维控制文档；真实 provider 业务证据、发布 artifact 和 CI 绑定、supervisor 执行、批准的 RPO/RTO、完整恢复与回滚演练、监控告警执行，以及 `QQ-EX-01` 至 `QQ-EX-06` 的独立 human-gated live QQ 验证仍未关闭。当前 `404 passed` 只代表受控本地工作树结果，不改变本文件各项原始状态，也不代表生产就绪或 provider 兼容性。

## 3. 状态、优先级与验收规则

状态必须使用以下五种值：

* `Confirmed Defect`：已有代码或测试直接证明的行为错误。
* `Required Control`：生产运行必须具备的约束、保护或流程，即使当前代码未表现为单一缺陷。
* `Operational Gap`：运行、部署、恢复或维护所需的产物或流程尚不完整。
* `Evidence Required`：实现可能已有部分基础，但当前证据不足以作出发布判断。
* `QQ-Excluded/Blocked`：依赖真实 QQ 服务器、公网环境或人工批准，本文件只登记排除原因，不创建实现任务。

优先级定义：

* `P0`：阻止生产发布，或可能造成错误业务事实、不可恢复数据损失、严重安全暴露。
* `P1`：生产前必须完成的高风险控制或证据；除非有明确批准的风险例外，不得带缺口发布。
* `P2`：不阻止首个受控发布，但必须有负责人、截止日期和后续版本门禁。

每个可执行工作项都必须填写：ID、类别、状态、优先级、当前发现、期望结果、验收标准、验证/证据、依赖、发布影响。需求代理应将每项拆成实现、测试、文档或运维子任务，但不得改变项的验收含义。

## 4. 可执行需求清单

### REQ-F01 知识 PendingAction 事件路由

* **类别**：功能正确性与持久化事件
* **状态**：`Confirmed Defect`
* **优先级**：`P0`
* **当前发现**： [event_consumers.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/event_consumers.py) 的 `consume_action_created` 无条件要求 `PendingAction.mail_analysis_id`，随后查找邮件分析和原始邮件。可是 [knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py) 创建 `AddKnowledge` 和 `RemoveKnowledge` 时没有邮件分析 ID。知识操作产生的 `PENDING_ACTION_CREATED` 因此可能进入错误的邮件确认通知路径并失败。
* **期望结果**：按 `action_type` 或等价的明确事件路由区分邮件状态提案与知识提案。邮件状态提案继续生成邮件关联的确认通知，知识提案走知识确认通知或既定知识确认处理路径，不依赖 `mail_analysis_id`。同一事件重复投递仍然幂等。
* **验收标准**：
  1. `AddKnowledge` 和 `RemoveKnowledge` 提案发布 `PENDING_ACTION_CREATED` 后，不读取 `mail_analysis_id`，可以生成正确的用户确认通知。
  2. 邮件分析产生的状态提案仍关联原始邮件和分析，并生成原有确认通知。
  3. 缺失或不匹配的聚合类型被安全拒绝并持久化可诊断错误，不产生半条通知。
  4. 同一事件重复消费不会重复通知、重复知识事实或重复 `ProcessedEvent`。
  5. 增加覆盖两类知识动作、邮件动作、重复投递和失败回滚的回归测试。
* **验证/证据**：运行相关 unit/integration suite；使用临时 SQLite 检查 Outbox、PendingAction、Notification、ProcessedEvent 状态；记录测试命令、通过数量和 schema head。证据引用上述两个源码文件及 [event_pipeline.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/event_pipeline.py)。
* **依赖**：现有 `PendingAction`、知识 proposal、Outbox 和 notification 模型；不依赖真实 QQ。
* **发布影响**：未完成前禁止生产发布，因为知识确认链路会产生可重试失败或无法完成的用户写操作。属于 P0 发布门禁。

### REQ-F02 QQ ACK 时序的确定性契约

* **类别**：功能正确性与可靠接收
* **状态**：`Confirmed Defect`
* **优先级**：`P0`
* **当前发现**： [infrastructure/qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py) 的本地 transport 会等待 `on_event` 返回后才返回普通 dispatch ACK。审计要求确认“持久化成功后 ACK，长任务不阻塞 ACK，持久化失败不确认”的精确语义，现有证据不足以证明所有路径满足该契约。
* **期望结果**：收到 dispatch 后先完成一次可验证的 durable receipt，再在限定时间内返回普通 ACK；LLM、工具、文件、Embedding、Chroma 等长任务不进入 ACK 前路径。持久化失败返回明确非成功响应，重复投递依据本地幂等记录处理。
* **验收标准**：
  1. fake request handler 能观察到 durable receipt 完成先于 ACK。
  2. 注入长任务时 ACK 不等待长任务完成。
  3. 注入持久化异常时不返回成功 ACK，且错误只包含安全分类。
  4. 重复 dispatch 不新增消息、AgentRun、文件或业务事实。
  5. 使用 fake transport 和临时 SQLite 增加顺序、异常、重复和取消场景测试，不需要 live QQ。
* **验证/证据**：记录相关测试、事件时间顺序断言、数据库行数和响应状态；引用 [qq_botpy.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/qq_botpy.py) 与 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)。
* **依赖**：现有 durable event receipt、Webhook intake、idempotency seam；可使用 fake gateway。
* **发布影响**：未完成前禁止生产发布，避免 QQ 重投、丢事件或 ACK 早于持久化造成不可解释状态。真实 QQ 收发仍不在本项验收范围。

### REQ-F03 被动回复目标语义

* **类别**：功能正确性与本地协议契约
* **状态**：`Confirmed Defect`
* **优先级**：`P0`
* **当前发现**： [agent/contracts.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/agent/contracts.py) 对 passive target 要求 `message_id` 和 `event_id`，对 proactive target 禁止被动元数据。审计仍需确认从入站事件持久化、回复目标重建到 deliver 的全链路不会把 passive 当 proactive，也不会丢失必要的序号字段。
* **期望结果**：被动回复只使用当前已持久化事件的 provider、scope、target、message ID、event ID 及可用序号；主动推送只使用主动目标字段。目标模式、缺字段和不可重放条件均有确定性结果。
* **验收标准**：
  1. 从持久化入站事件重建 passive target，并保留要求的字段。
  2. passive target 缺少 `message_id` 或 `event_id` 时在本地边界失败，不降级为 proactive。
  3. proactive target 携带 passive 元数据时被拒绝。
  4. 重放同一本地事件不会改变目标模式或生成重复业务事实。
  5. 增加 fake gateway 对 reply、deliver、push 的分流测试，并覆盖序号存在和缺失两种合法输入。
* **验证/证据**：运行 agent、QQ adapter、conversation integration tests，保存目标对象字段断言和 fake gateway 调用记录；引用 [agent/contracts.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/agent/contracts.py)、[infrastructure/adapters/protocols.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/adapters/protocols.py) 和 [docs/architecture.md](/Users/panafish/workstation/jobs_status_managerment/docs/architecture.md)。
* **依赖**：本地入站事件模型、ReplyTarget、fake gateway；不依赖真实 QQ。
* **发布影响**：未完成前禁止生产发布，因为回复可能发错模式、无法回复当前消息或在重放时产生错误副作用。

### REQ-F04 本地重放安全与持久化事件契约

* **类别**：可靠性与功能回归
* **状态**：`Required Control`
* **优先级**：`P1`
* **当前发现**：架构规定 At-least-once Delivery 加 Idempotent Consumer，但各事件类型和故障窗口需要统一的可执行证据。
* **期望结果**：邮件、Webhook、确认、通知、知识索引和清理任务在进程退出、重复投递、外部调用前后异常时，都能通过数据库状态继续、重试或进入明确终态，且不重复业务事实。
* **验收标准**：每类 durable event 至少有一次重复投递测试、一次外部调用前异常测试、一次外部调用后异常测试；`ProcessedEvent` 唯一性、业务唯一键、attempt、last error、next attempt 和终态均可查询；文档明确“不声称 exactly-once”。
* **验证/证据**：临时 SQLite integration suite、故障注入记录、重启前后数据库快照和 [docs/implementation-plan.md](/Users/panafish/workstation/jobs_status_managerment/docs/implementation-plan.md) 不变量核对。
* **依赖**：REQ-F01、REQ-F02、REQ-F03；现有 Outbox 和 worker 状态模型。
* **发布影响**：所覆盖的任务类别若无证据，不得进入生产。未覆盖类别至少形成 P1 风险例外记录。

### REQ-CFG01 测试环境隔离工作目录 `.env`

* **类别**：配置与测试可重复性
* **状态**：`Confirmed Defect`
* **优先级**：`P0`
* **当前发现**： [config/settings.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/config/settings.py) 默认从当前工作目录加载 `.env`。`tests/conftest.py` 虽然构造显式临时设置，但当前默认 `uv run pytest -q` 仍有 ambient `.env` 泄漏到 readiness fixture，结果是 `373 passed/2 failed`。
* **期望结果**：测试默认不读取开发者工作目录 `.env` 或其他未声明的环境状态；需要环境变量的测试必须显式注入并在测试结束后恢复。生产配置加载行为可以继续读取约定来源，但测试入口必须隔离。
* **验收标准**：
  1. 在存在启用 IMAP/LLM 值的工作目录 `.env` 时，默认全套测试仍为 `375 passed`。
  2. 在无 `.env`、带 `.env`、带外部环境变量三种条件下，readiness fixture 结果一致。
  3. 测试不会读取真实凭证、真实路径或真实外部 endpoint。
  4. CI 使用干净工作目录或显式 disabled 环境并记录方式。
* **验证/证据**：分别执行默认工作目录和隔离临时目录的 `uv run pytest -q`，结果均为 `375 passed`；执行 readiness tests 并记录 IMAP/LLM 为 `disabled`。引用 [tests/conftest.py](/Users/panafish/workstation/jobs_status_managerment/tests/conftest.py)、[tests/e2e/test_health.py](/Users/panafish/workstation/jobs_status_managerment/tests/e2e/test_health.py) 和 [tests/unit/test_lifecycle.py](/Users/panafish/workstation/jobs_status_managerment/tests/unit/test_lifecycle.py)。
* **依赖**：测试 fixture 和 settings source precedence 的选择；不得修改真实凭证文件。
* **发布影响**：未完成前禁止把测试绿灯作为发布证据，且 P0 发布门禁关闭。

### REQ-CFG02 Alembic 指向应用同一数据库

* **类别**：数据库配置与发布一致性
* **状态**：`Operational Gap`
* **优先级**：`P0`
* **当前发现**： [alembic.ini](/Users/panafish/workstation/jobs_status_managerment/alembic.ini) 的静态 URL 是 `sqlite:///./jobs_status_alembic.db`，而应用通过 `APP_DATABASE_PATH` 使用另一数据库。虽然程序化 migration runner 会覆盖 URL，直接执行 Alembic 命令可能目标错误文件。
* **期望结果**：所有支持的 migration 命令，包括 `uv run alembic check/current/upgrade`，都明确指向当前应用数据库，或明确要求传入同一数据库 URL，禁止静默创建旁路数据库。
* **验收标准**：
  1. 在临时应用数据库上执行 migration 和 `alembic check`，检查的 revision 与应用 health 一致。
  2. 命令不会在仓库根目录创建旁路 `jobs_status_alembic.db`。
  3. 数据库路径来源、相对路径解析和 CI 参数在部署文档中写清楚。
  4. 失败时显示安全的路径类别和配置字段，不显示凭证。
* **验证/证据**：记录 `uv run alembic check`、`uv run alembic current`、应用 `health` 和 SQLite `schema_migrations` 的一致结果；引用 [migrations/env.py](/Users/panafish/workstation/jobs_status_managerment/migrations/env.py)、[infrastructure/database/migrations.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/database/migrations.py) 和 [alembic.ini](/Users/panafish/workstation/jobs_status_managerment/alembic.ini)。
* **依赖**：数据库路径注入方案、migration runner、REQ-CFG01 的隔离原则。
* **发布影响**：未完成前禁止执行生产 schema 发布，防止应用和迁移检查不同库。

### REQ-CFG03 健康、存活、就绪和 durable task 语义

* **类别**：配置、可观测性与运行契约
* **状态**：`Required Control`
* **优先级**：`P0`
* **当前发现**： [application/health.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/application/health.py) 已返回数据库、schema、readiness、durable task 和失败任务字段，但现有文档主要把 `/health` 称为 readiness，未形成独立的 liveness、readiness、durable task 处置契约。
* **期望结果**：定义并记录三类语义：liveness 只表示进程可响应，readiness 表示服务可接收工作且 schema 合法，durable task 状态表示任务是否可恢复、重试或需要人工处理。健康接口不探测或暴露不必要的外部细节。
* **验收标准**：
  1. 文档和测试明确各检查项、HTTP 状态、失败条件和 supervisor 行为。
  2. 数据库不可用、schema 非 head、任务失败、任务陈旧、外部能力未配置时的结果可预测。
  3. liveness 不因短暂外部依赖失败而误报进程死亡；readiness 不在 schema 未就绪时返回成功。
  4. 响应不含业务正文、凭证、完整 provider payload 或敏感路径。
* **验证/证据**：扩展 [tests/e2e/test_health.py](/Users/panafish/workstation/jobs_status_managerment/tests/e2e/test_health.py)，运行本地 HTTP driver，记录各状态码和 JSON 字段；同步 [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 与 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)。
* **依赖**：现有 health payload、lifecycle、migration head 和 durable task 查询。
* **发布影响**：未完成前禁止接入生产 supervisor 或监控告警，避免错误重启或错误接流量。

### REQ-CFG04 历史验证记录与当前证据对账

* **类别**：发布证据治理
* **状态**：`Evidence Required`
* **优先级**：`P1`
* **当前发现**：README 和 implementation plan 保留了 Phase 6 的 `120 tests`、`337 passed` 等历史快照；修复前默认结果为 `373 passed + 2 failed`，当前受控工作树为 `404 passed`。旧数字若无日期和范围标注会误导发布判断。
* **期望结果**：所有验证记录区分历史阶段、当前工作树、测试环境和测试范围；当前证据统一引用 `404 passed` 的受控工作树结果、Alembic head 和实际命令。
* **验收标准**：历史数字保留时带日期、范围和“不可替代当前基线”说明；当前文档记录默认失败原因、显式 disabled 通过结果、Alembic 检查和禁止的 live QQ 项；不存在把 `110/108` 或其他旧数量写成当前事实的内容。
* **验证/证据**：逐项审阅 [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md)、[docs/implementation-plan.md](/Users/panafish/workstation/jobs_status_managerment/docs/implementation-plan.md)、[docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md) 与本文，附当前命令输出摘要。
* **依赖**：REQ-CFG01、当前 CI 执行结果。
* **发布影响**：P1 证据未对账前，发布审批必须保持阻塞，不能以历史绿灯替代当前证据。

### REQ-SEC01 输入、附件和解析资源上限

* **类别**：安全与数据保护
* **状态**：`Required Control`
* **优先级**：`P0`
* **当前发现**：settings 已有上传、下载、agent 和 IMAP 批量上限，知识契约对查询和 top K 有边界，但生产需求还必须统一覆盖 request body、附件、解析器、DOCX/PDF 解压膨胀、文本块和 embedding 输入。
* **期望结果**：所有外部输入在进入解析、LLM、工具、向量索引和持久化前都有字节、数量、时间、递归或字符上限；DOCX/PDF 解压和解析使用总展开大小、页数、对象数、文本字符数和 chunk 数限制，超限进入安全失败状态。
* **验收标准**：
  1. 超过请求、附件、下载、解析、文本、chunk、tool call、LLM result 任一上限时拒绝或进入可恢复失败，不继续调用下游。
  2. DOCX zip bomb、异常 PDF、超长文本、多附件和高 chunk 数 fixture 均有测试。
  3. 超时、内存或数量限制不会留下半条知识文档或可搜索的未完成索引。
  4. 每个上限有配置名、默认值、单位、责任组件和测试证据。
* **验证/证据**：边界和超限测试、临时目录占用检查、失败状态数据库快照；引用 [config/settings.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/config/settings.py)、[knowledge/contracts.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/contracts.py) 和 [docs/configuration.md](/Users/panafish/workstation/jobs_status_managerment/docs/configuration.md)。
* **依赖**：现有 upload/parser/LLM/Chroma seams；不依赖 live QQ。
* **发布影响**：未完成前禁止接收不受信任的生产附件或启用生产知识解析。

### REQ-SEC02 上传路径、持久化文件权限和文件名隔离

* **类别**：安全与文件系统
* **状态**：`Required Control`
* **优先级**：`P0`
* **当前发现**： [infrastructure/paths.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/paths.py) 创建目录时请求 `0700`，部署文档也要求私有目录，但必须证明上传目标、临时 `.part` 文件、归档文件和 Chroma 路径均无法通过路径穿越或链接逃逸。
* **期望结果**：所有 durable 文件路径由受控根目录解析，拒绝绝对路径、`..`、符号链接逃逸和不安全文件名；数据库、WAL、uploads、Chroma、backups 和临时文件为 owner-only；权限加固失败时 fail closed 或报告 degraded/unavailable，不静默继续；失败清理不遗留可读临时文件。
* **验收标准**：路径穿越、符号链接、Unicode 等价路径、重复文件名、异常中断和权限不足均有测试；成功和失败后检查 realpath、目录和数据库文件权限、目录内容和 `.part` 文件；强制 `chmod` 失败时不会声称正常就绪，也不会保留已写入的敏感文件；应用不把用户文件直接作为静态资源暴露。
* **验证/证据**：临时文件系统安全测试、`stat` 权限记录、部署检查清单；引用 [paths.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/infrastructure/paths.py)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 和 [docs/configuration.md](/Users/panafish/workstation/jobs_status_managerment/docs/configuration.md)。
* **依赖**：上传存储服务、应用数据目录和部署用户权限。
* **发布影响**：未完成前禁止生产上传，防止任意文件写入、越权读取或敏感数据泄漏。

### REQ-SEC06 AddKnowledge 源文件归属与数据血缘

* **类别**：安全、授权与数据一致性
* **状态**：`Confirmed Defect`
* **优先级**：`P0`
* **当前发现**： [knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py) 的 `propose_add` 接受 `source_file_id`，但不检查文件存在或属于当前用户；知识执行边界也可能只复制该 ID。外键只能保证引用存在，不能保证 `UserFile.user_id` 与提案用户一致。
* **期望结果**：AddKnowledge 在创建提案时验证源文件存在且属于提案用户，在执行持久化时再次验证同一条件；无效或跨用户源文件不能生成 PendingAction 或 KnowledgeDocument，且错误不泄露其他用户数据。
* **验收标准**：
  1. 不存在的 `source_file_id` 被拒绝，不创建 PendingAction 或 KnowledgeDocument。
  2. 另一用户的 `source_file_id` 被拒绝，响应和日志不泄露该文件的标题、内容或存在性细节。
  3. 在提案创建后改变或删除源文件时，执行边界再次拒绝，不产生孤立知识记录。
  4. 两用户、缺失源文件、正常源文件和重复执行均有 integration tests。
* **验证/证据**：临时 SQLite 双用户测试、提案和执行前后行数快照、错误安全性扫描；引用 [knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py)、[knowledge/models.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/models.py) 和 [docs/architecture.md](/Users/panafish/workstation/jobs_status_managerment/docs/architecture.md)。
* **依赖**：UserFile 模型、AddKnowledge proposal/execution、PendingAction confirmation transaction。
* **发布影响**：未完成前禁止生产知识导入，防止跨用户数据血缘和授权绕过；属于 P0 发布门禁。

### REQ-SEC03 保留、删除和备份擦除一致性

* **类别**：安全与数据保护
* **状态**：`Required Control`
* **优先级**：`P0`
* **当前发现**：SQLite 是知识事实源，Chroma 是可重建索引，uploads、日志和 backups 另有副本。当前材料没有给出跨数据库、上传目录、Chroma、日志和备份的统一 retention 与 erasure 证据。
* **期望结果**：定义数据分类、保留期限、删除触发、删除范围、异步清理失败处理和备份到期策略。删除知识或用户数据后，数据库、上传文件、Chroma 记录、日志关联数据和仍在保留期内的备份均有明确状态。
* **验收标准**：
  1. 每类数据有 owner、保留期限、删除方式和不可立即删除时的状态。
  2. `REMOVED` 文档不再检索，Chroma cleanup 可重试且幂等。
  3. 用户级删除演练检查 DB、uploads、Chroma、日志索引和备份策略，不声称已从不可变备份立即擦除。
  4. 清理失败可查询、报警和重试，不影响其他文档。
* **验证/证据**：临时 SQLite、uploads、Chroma fake 或本地实例的删除演练，记录对象计数、检索结果和失败重试；引用 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)、[knowledge/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/knowledge/proposals.py) 和 [docs/architecture.md](/Users/panafish/workstation/jobs_status_managerment/docs/architecture.md)。
* **依赖**：备份策略、日志策略、知识 cleanup worker、REQ-SEC02。
* **发布影响**：未完成前禁止承诺生产数据删除或接收高敏感个人资料。

### REQ-SEC04 安全日志、健康暴露与 secret 注入基线

* **类别**：安全运营
* **状态**：`Required Control`
* **优先级**：`P0`
* **当前发现**：配置和 adapter 已有 SecretStr、safe error 及健康不返回业务数据的基础，但生产仍需统一验证日志、健康端点、仓库和 secret 注入方式。
* **期望结果**：凭证、Authorization、邮箱正文、prompt、provider payload、签名、完整 openid、附件内容和 URL query 永不进入日志、证据或仓库；secret 通过受控注入提供，配置示例无真实值；健康端点只暴露最少状态，默认绑定私有地址。
* **验收标准**：
  1. 对配置失败、IMAP/LLM/QQ 错误、上传失败、health、backup 和 retry 命令做日志扫描，敏感样本不出现。
  2. `git` 基线检查确认 `.env`、数据库、uploads、Chroma、backups 和临时证据被忽略且仓库无 secret。
  3. secret manager 或受控环境注入流程有示例和轮换责任人，不把 secret 写入 identity 字段。
  4. 非授权网络无法访问生产健康和管理命令；健康响应不含内部 endpoint 或完整路径。
* **验证/证据**：合成敏感值的日志回归测试、仓库 secret scan、监听地址检查、health HTTP 检查；引用 [config/settings.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/config/settings.py)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 和 [docs/configuration.md](/Users/panafish/workstation/jobs_status_managerment/docs/configuration.md)。
* **依赖**：部署和 CI 环境、日志 sink、secret 注入工具。
* **发布影响**：未完成前禁止生产凭证注入和公网暴露任何管理或健康 surface。

### REQ-SEC05 确认码节流与重放保护

* **类别**：安全与人工确认
* **状态**：`Required Control`
* **优先级**：`P1`
* **当前发现**：PendingAction 使用确定性确认码和过期时间，但审计要求确认码尝试的速率限制、失败计数、账户级保护和重放语义有明确控制。
* **期望结果**：确认码按用户、会话、来源和时间窗口进行有界尝试；失败尝试可安全计数，过期、已解决和错误用户不能改变业务事实，重复成功确认保持幂等。
* **验收标准**：超过阈值进入冷却或终态；错误码不泄露有效码存在性；过期、拒绝、重复确认各有测试；数据库重启后节流和状态仍可解释；不使用仅存于进程内存的唯一保护。
* **验证/证据**：临时 SQLite 加 fake clock 的单元和集成测试，记录状态转移、计数和响应；引用 [application_core/proposals.py](/Users/panafish/workstation/jobs_status_managerment/src/jobs_status_manager/application_core/proposals.py)、[docs/architecture.md](/Users/panafish/workstation/jobs_status_managerment/docs/architecture.md)。
* **依赖**：PendingAction schema、确认 command router、durable transaction。
* **发布影响**：未完成前禁止开放生产写操作确认入口，P1 发布门禁关闭。

### REQ-OPS01 可复现进程监督与部署产物

* **类别**：部署与运行
* **状态**：`Operational Gap`
* **优先级**：`P1`
* **当前发现**：系统规定单用户、单邮箱、单 QQ 账号、单进程，文档描述了 supervisor、SIGTERM 和关闭顺序，但缺少可复现、版本化的实际监督配置或等价部署产物。
* **期望结果**：交付一份与目标运行环境匹配的 supervisor artifact，固定工作目录、用户、环境注入、单实例锁、启动顺序、重启策略、资源限制、日志位置和 SIGTERM 行为；明确不能对同一 SQLite 启动第二进程。
* **验收标准**：从干净目录安装、迁移、bootstrap、启动、health、停止、重启可重复；异常退出可按策略恢复；没有第二 listener 或第二 botpy server；部署产物不含凭证。
* **验证/证据**：在隔离主机或等价本地环境执行一次完整演练，记录进程、监听、health、退出码和数据库 revision；引用 [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 与 [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md)。
* **依赖**：REQ-CFG02、REQ-CFG03、备份和 secret 注入方案。
* **发布影响**：未完成前禁止生产部署，P1 发布门禁关闭。

### REQ-OPS02 CI 质量与安全门禁

* **类别**：持续集成与证据
* **状态**：`Operational Gap`
* **优先级**：`P1`
* **当前发现**：当前命令已有 Ruff、format、basedpyright、pytest、Alembic、uv lock 和 diff 检查记录，但需要形成固定 CI gates，并明确环境隔离和 live QQ 排除。
* **期望结果**：CI 在干净环境运行受控 disabled 全套测试，并执行格式、类型、迁移一致性、依赖锁定、diff、secret scan、资源边界、权限和回归测试；live QQ 不因缺少凭证而伪造成功，也不成为本文件的实现任务。
* **验收标准**：所有 gate 失败即阻止 artifact 发布；测试结果包含 `375 passed` 的当前受控基线或明确更新后的事实；`uv run alembic check` 通过；CI 日志不含秘密；失败项可追溯到 commit 和 artifact 版本。
* **验证/证据**：执行一次完整 CI pipeline 或等价本地命令组，保存命令、退出码、测试数量、head 和扫描摘要；引用 [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md)。
* **依赖**：REQ-CFG01、REQ-CFG02、REQ-SEC04、依赖锁定文件。
* **发布影响**：未完成前禁止生成可发布 artifact。

### REQ-OPS03 发布 artifact、版本和可追溯性

* **类别**：发布工程
* **状态**：`Operational Gap`
* **优先级**：`P1`
* **当前发现**：当前文档记录代码和 SDK 版本片段，但没有统一的发布 artifact、版本号、来源 commit、migration head、依赖锁和证据索引要求。
* **期望结果**：每次发布产出不可变 artifact，包含应用版本、来源 commit、Python/uv/SDK 版本、依赖锁摘要、schema head、构建时间和验证报告索引，不包含凭证或用户数据。
* **验收标准**：同一版本可在干净环境复现安装；artifact 内容与源码 commit、lockfile 和 migration 目录一致；启动前可检查版本和 head；版本冲突或 schema 不匹配时停止发布。
* **验证/证据**：构建并安装一次 artifact，运行 health、测试和 `alembic check/current`，保存校验和及元数据清单；引用 [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md) 和 [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md)。
* **依赖**：REQ-OPS02、REQ-CFG02、依赖/SBOM 流程。
* **发布影响**：未完成前禁止标记正式版本或把构建物交给生产环境。

### REQ-OPS04 SQLite、uploads、Chroma 备份与恢复目标

* **类别**：备份、灾难恢复与数据保护
* **状态**：`Operational Gap`
* **优先级**：`P0`
* **当前发现**：已有 `backup`、`restore-check`、SQLite backup API、Chroma rebuild 和 owner-private 要求，但没有明确覆盖 SQLite 加 WAL、uploads、Chroma、备份副本的完整策略，也没有 off-host retention、RPO/RTO 数值和证明。
* **期望结果**：定义备份对象、频率、加密、保留、off-host 位置、访问控制、RPO、RTO、失败报警和恢复顺序。SQLite 是知识事实源，Chroma 可由 SQLite 和上传数据重建，恢复不能把不一致索引当作事实源。
* **验收标准**：
  1. 备份包含数据库一致副本、uploads 和可重建 Chroma 所需输入或明确可重建来源。
  2. off-host 至少保留一份，保留策略、加密和访问权限可审计。
  3. 明确可接受 RPO 和 RTO 数值，不能用“尽快”替代。
  4. 恢复到隔离目录后通过 schema、integrity、业务计数和 Chroma rebuild 检查，不覆盖线上库。
* **验证/证据**：备份清单、恢复脚本输出、校验和、隔离恢复数据库、文件计数、Chroma rebuild 计数和目标 RPO/RTO 记录；引用 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md) 与 [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md)。
* **依赖**：REQ-SEC02、REQ-SEC03、REQ-OPS05、单进程停机或 quiesce 方案。
* **发布影响**：没有可证明恢复路径前禁止生产发布，P0 发布门禁关闭。

### REQ-OPS05 备份恢复演练与运行手册

* **类别**：运维流程
* **状态**：`Operational Gap`
* **优先级**：`P1`
* **当前发现**：deployment 和 operations 已有启动、失败查看、retry、backup、restore-check、integrity-check、rebuild-chroma 命令，但尚未形成覆盖故障、升级、回滚、删除和告警的运行手册及定期演练记录。
* **期望结果**：提供最小可操作 runbooks，覆盖启动失败、数据库锁、schema 不一致、任务陈旧、QQ 无法使用时的本地模式、LLM/IMAP 失败、上传失败、Chroma 重建、备份恢复、数据删除、secret 轮换和事故升级。
* **验收标准**：值班人员可在无代码修改的前提下按手册完成健康检查、失败定位、受控 retry、隔离恢复和回滚；每项注明停止条件、不可执行操作、数据影响和证据保存规则；至少完成一次 restore drill 和一次异常重启 drill。
* **验证/证据**：带时间戳的演练记录、命令输出、恢复耗时、RPO/RTO 对照、残留文件检查和负责人签字；引用 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md) 与 [docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md)。
* **依赖**：REQ-OPS04、REQ-CFG03、REQ-OPS01。
* **发布影响**：P1 手册和演练证据缺失时禁止生产发布，除非批准风险例外。

### REQ-OPS06 依赖、漏洞和 SBOM 流程

* **类别**：供应链与发布治理
* **状态**：`Required Control`
* **优先级**：`P1`
* **当前发现**：项目使用 uv lock、botpy SDK、httpx2、SQLAlchemy、Alembic、Chroma 等外部依赖，当前有锁文件检查，但没有持续依赖漏洞、许可证、SBOM、升级验证和回滚责任流程。
* **期望结果**：每个发布 artifact 生成准确 SBOM，记录直接和传递依赖、版本、来源、许可证和漏洞状态；依赖升级经过测试、迁移检查、资源边界检查和回滚评估。
* **验收标准**：CI 可生成并保存 SBOM；高危漏洞有阻断规则或批准的风险例外；`uv lock --check`、测试、静态检查和 Alembic check 与 SBOM 绑定到版本；botpy、LLM、解析器和 Chroma 变更有兼容性评估。
* **验证/证据**：当前 artifact 的 SBOM、扫描报告、例外记录、升级 PR 验证结果和版本化责任矩阵；引用 [README.md](/Users/panafish/workstation/jobs_status_managerment/README.md) 中的依赖与验证快照。
* **依赖**：REQ-OPS02、REQ-OPS03、供应链扫描工具和 artifact 存储。
* **发布影响**：未完成前禁止正式发布未审计依赖，至少阻止高危漏洞版本进入生产。

### REQ-OPS07 周期性证据刷新与容量复核

* **类别**：持续运维与后续版本治理
* **状态**：`Evidence Required`
* **优先级**：`P2`
* **当前发现**：当前证据是单次审计快照，任务失败、数据库增长、uploads、Chroma、日志、备份占用和外部调用耗时没有持续趋势记录。
* **期望结果**：为受控生产运行建立周期性复核，刷新测试、schema、依赖、备份恢复、权限、资源上限、任务积压和磁盘容量证据；任何超出阈值的结果都能触发升级或后续版本门禁。
* **验收标准**：定义复核频率、负责人、阈值和保留期限；至少覆盖全套本地测试、Alembic check、失败任务、stale task、数据库/WAL、uploads、Chroma、backups 和日志容量；复核报告关联 artifact 版本和 commit；超阈值有记录的处置结果。
* **验证/证据**：提交一份模拟周期复核报告、阈值表、容量样本和升级记录；引用 [docs/operations.md](/Users/panafish/workstation/jobs_status_managerment/docs/operations.md)、[docs/deployment.md](/Users/panafish/workstation/jobs_status_managerment/docs/deployment.md) 和 [docs/imap-llm-adapter-verification.md](/Users/panafish/workstation/jobs_status_managerment/docs/imap-llm-adapter-verification.md)。
* **依赖**：REQ-OPS02、REQ-OPS03、REQ-OPS04、REQ-OPS05。
* **发布影响**：不阻止首个受控发布，但未完成时必须登记为 P2 运维风险，并在后续版本门禁中关闭，不得长期无负责人遗留。

## 5. 证据登记册

### 5.1 已有的本地确定性证据

| 证据 ID | 命令或范围 | 当前结果 | 使用限制 |
|---|---|---|---|
| E-LOCAL-01 | 修复前默认 `uv run pytest -q` | `373 passed + 2 failed`，约 `137.61s` | 修复前历史结果，失败暴露工作目录 `.env` 泄漏，不得称为全套通过。 |
| E-LOCAL-02 | 当前受控显式 disabled 配置下的 `uv run pytest -q` | `404 passed` | 仅证明当前工作树在隔离配置下的本地确定性测试，不证明外部服务。 |
| E-LOCAL-03 | `uv run alembic check` | PASS，无新的 upgrade operation | 仍需由 REQ-CFG02 证明目标库与应用相同。 |
| E-LOCAL-04 | `uv run alembic current` | `0008_qq_reply_targets (head)` | 仅证明被检查数据库的 head。 |
| E-LOCAL-05 | `uv run pytest -q tests/unit/test_openai_compatible_llm.py` | `31 passed` | 证明 adapter contract 和 mock transport，不证明 provider 全兼容。 |
| E-LOCAL-06 | Ruff、format、basedpyright、uv lock、diff checks | 已有 PASS 记录 | 必须在发布 artifact 对应 commit 上重跑。 |
| E-LOCAL-07 | 临时 SQLite、fake gateway、MockTransport、Starlette health | 已验证部分事务、幂等、readiness、错误和资源 ownership | 必须补足本文件 P0/P1 项所需的故障和安全边界。 |

### 5.2 待补的非 QQ 证据

需求代理必须为每项证据记录命令、commit 或 artifact 版本、运行时间、环境隔离方式、结果、失败原因、相关日志位置和敏感数据清理结果。

| 证据 ID | 对应需求 | 必须产生的证据 |
|---|---|---|
| E-REQ-01 | REQ-F01 | AddKnowledge、RemoveKnowledge、邮件状态提案的事件路由和重复消费回归结果。 |
| E-REQ-02 | REQ-F02 | durable receipt 与 ACK 的先后顺序、长任务不阻塞、持久化失败不 ACK。 |
| E-REQ-03 | REQ-F03 | passive/proactive target 字段、模式分流、重放和缺字段失败结果。 |
| E-REQ-04 | REQ-CFG01 | 带 ambient `.env`、无 `.env`、显式 disabled 三种环境均为 `375 passed` 的记录。 |
| E-REQ-05 | REQ-CFG02 | Alembic、应用 health、`schema_migrations` 读取同一临时数据库的记录。 |
| E-REQ-06 | REQ-CFG03 | liveness/readiness/durable task 状态矩阵和 HTTP driver 结果。 |
| E-REQ-07 | REQ-SEC01 through REQ-SEC06 | 超限、路径、权限失败、源文件归属、删除、日志、secret、确认码节流的负向测试和清理结果。 |
| E-REQ-08 | REQ-OPS01 through REQ-OPS03 | 干净环境部署、CI gates、artifact metadata、版本和 SBOM。 |
| E-REQ-09 | REQ-OPS04 and REQ-OPS05 | off-host backup、隔离 restore、Chroma rebuild、restore drill、RPO/RTO。 |

### 5.3 证据保存规则

证据只能保存必要的状态、计数、时间、版本、HTTP 状态、错误分类和脱敏 ID。不得保存凭证、Authorization、签名、完整 openid、邮件正文、prompt、provider payload、附件内容、URL query 或私有数据库副本。所有临时数据库、上传、Chroma、日志和脚本必须在证据确认后清理。

## 6. QQ 服务器依赖的排除登记册

以下项目明确标记为 `QQ-Excluded/Blocked`，不属于本文的可执行需求，也不应被需求代理改写成当前实现任务：

| 排除 ID | 项目 | 状态 | 排除原因 |
|---|---|---|---|
| QQ-EX-01 | Webhook 公网绑定、公开 endpoint 和 TLS 验证 | `QQ-Excluded/Blocked` | 需要公网环境和人工批准；本文件只要求本地 transport 契约。 |
| QQ-EX-02 | URL challenge 的真实 QQ 交互 | `QQ-Excluded/Blocked` | 需要真实 QQ token/API endpoint 和批准凭据。 |
| QQ-EX-03 | 真实 C2C send/receive、主动推送和被动收发 | `QQ-Excluded/Blocked` | 需要真实账号、recipient 和 provider 结果；本文件只覆盖 REQ-F02、REQ-F03 的本地确定性代码契约。 |
| QQ-EX-04 | QQ 文件、图片能力和真实附件下载 | `QQ-Excluded/Blocked` | 需要真实 provider capability 验证；本文件仍要求通用输入上限、路径 containment 和解析安全。 |
| QQ-EX-05 | 真实 provider ambiguous outcome | `QQ-Excluded/Blocked` | 需要受控网络中断和人工判断；本文件只要求本地结果分类和重放安全。 |
| QQ-EX-06 | 在线 QQ 重启恢复 | `QQ-Excluded/Blocked` | 需要真实在线会话和人工 smoke；本文件只要求本地 lifecycle、durable state 和 supervisor 演练。 |

排除项不得被标成 PASS，也不得从项目总风险中删除。它们应在后续获得公网、凭据、收件人、清理和回滚批准后，作为单独的 human-gated QQ 验证流程处理。

## 7. 推荐依赖波次

### Wave 0，证据和隔离基线

完成 REQ-CFG01、REQ-CFG02、REQ-CFG04、REQ-OPS02 的测试环境、数据库目标和当前证据对账。当前本地参考结果为受控 `404 passed`；波次结束仍需在对应发布环境中稳定重现，并确认测试不使用真实外部能力。

### Wave 1，P0 代码和安全控制

完成 REQ-F01、REQ-F02、REQ-F03、REQ-CFG03、REQ-SEC01、REQ-SEC02、REQ-SEC03、REQ-SEC04、REQ-SEC06。所有项必须有负向测试、持久化状态检查和敏感数据清理证据。

### Wave 2，恢复和生产工程

完成 REQ-OPS01、REQ-OPS03、REQ-OPS04、REQ-OPS05、REQ-OPS06、REQ-SEC05，以及 Wave 1 的回归验证。形成可部署 artifact、监督配置、备份恢复演练、SBOM 和运行手册。

### Wave 3，发布前复核

重新执行所有 P0/P1 gates，核对 schema head、artifact、依赖、权限、日志、恢复目标和证据登记册。任何失败都回到对应波次，不以 QQ 排除项的 BLOCKED 状态伪造通过。

## 8. 发布门禁与风险例外

生产发布必须同时满足：

1. 所有 P0 工作项关闭，且每项有对应实现、回归测试和证据。
2. 所有要求在生产前完成的 P1 工作项关闭，尤其是 P1 证据、CI gates、artifact/version、backup/restore、runbook、SBOM 和确认码节流。
3. 受控全套测试为当前工作树的通过结果，当前基线应记录 `404 passed`，不是修复前的 `373 passed + 2 failed`，也不是旧的 `110/108`、`120` 或 `337` 快照。
4. `uv run alembic check` 通过，应用检查的数据库与 migration 检查的数据库相同，head 为 `0008_qq_reply_targets` 或经批准的新 head。
5. artifact、版本、依赖锁、SBOM、权限、secret 注入、健康语义、备份恢复和 RPO/RTO 证据相互对应。
6. QQ 服务器依赖项继续保持 `QQ-Excluded/Blocked`，不得将排除项解释为本文件已完成，也不得以排除项掩盖 QQ 独立的 P0/P1 代码和控制缺陷。

所有 P0 项和所需的 P1 证据必须在生产发布前关闭，除非有明确记录的风险例外批准。风险例外必须包含：受影响 ID、具体风险、范围、补偿控制、负责人、批准人、有效期、回滚条件和下一次复核日期。没有这些字段的口头同意不构成发布授权。

本文件完成后仍不能宣称项目生产就绪，只有满足上述门禁并完成独立发布审批，才可作出该判断。
