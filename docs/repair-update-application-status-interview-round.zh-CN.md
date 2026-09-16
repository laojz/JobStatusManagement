# UpdateApplicationStatus 参数缺失修复需求

## 1. 缺陷定级与目的
- 缺陷类型：`Confirmed Defect`
- 优先级：`P0`

用户表达了有效写入意图，但系统生成的 `UpdateApplicationStatus` 参数不满足业务契约，
导致 `AgentRun` 失败，且没有生成供用户确认的提案。

本文件用于直接交给 coding agent 执行。修复应让参数缺失或含义不明确的写入请求进入
可恢复的澄清路径，同时保留确认门禁、领域校验和数据安全边界。

除非本文件明确要求，不得扩大修改范围。

## 2. 已确认事实与证据边界
### 2.1 报告语句
```text
新增小米求职状态
```

该语句表达了写入意图，但没有完整给出岗位、状态和面试轮次。

数据库记录能够证明实际生成并持久化的工具参数，以及随后发生的参数校验失败。数据库
证据不能还原模型的完整推理过程，也不能据此断言模型或 provider 本身存在缺陷。

### 2.2 2026-09-15 生产证据
```text
AgentRun ID:        3ce4aec2-9c62-431c-b291-039e4eeabd34
ToolCall ID:        012c6a5f-c927-48e5-b68f-c909b56d9ff4
sequence:           1
tool:               UpdateApplicationStatus
arguments:          {"company":"小米","position":"软件研发工程师","status":"INTERVIEW"}
provider arguments: {"company":"小米","position":"软件研发工程师","status":"INTERVIEW"}
persisted error:    malformed arguments for UpdateApplicationStatus
tool call time:     2026-09-15 08:13:57.868956
```

`arguments` 与 provider arguments 在语义上等于上述 JSON。实现本修复不需要完整的
provider call ID，因此本文不记录该值，也不得把补采该值作为前置条件。

工具调用时间来自数据库。数据库时区必须通过部署配置或连接行为核实，不能直接假定为
UTC、CST 或其他时区。

证据确认了以下事实：
1. 系统选择了 `UpdateApplicationStatus`；
2. 工具参数含公司、岗位和 `INTERVIEW`，但没有 `interview_round`；
3. 当前参数契约拒绝该输入；
4. 持久化错误为 `malformed arguments for UpdateApplicationStatus`；
5. 对应运行没有完成正常澄清或确认提案流程。

证据不能证明模型为何选择岗位或状态，也不能证明更换 provider 或模型即可修复问题。
不得补写未记录的 provider 错误、日志、测试结果、提交哈希或部署版本。

## 3. 当前代码与精确根因
`StatusUpdateArguments` 当前字段为：
- `company`：必填；
- `department`：可选；
- `position`：必填；
- `status`：必填枚举；
- `interview_round`：可选正整数。

其 Pydantic `model_validator` 在运行时要求：`INTERVIEW` 必须提供正数
`interview_round`，非 `INTERVIEW` 状态禁止提供该字段。

生产参数缺少面试轮次，所以 `UpdateApplicationStatusArguments` 校验抛出
`ValidationError`。

`turn_runtime._resume_pending_tools()` 捕获异常后执行以下路径：
1. 用硬编码通用消息替换具体校验详情；
2. 生成 `malformed arguments for UpdateApplicationStatus`；
3. 通过 `persist_tool_error` 持久化该错误；
4. 返回终止性错误，不再产生自然语言澄清或最终回复。

直接根因是可处理的参数校验失败被转换成终止性 `AgentRun` 失败。

当前工具定义使用 `model_json_schema()`，写入描述为通用的
`Propose <name>; confirmation is required`。Pydantic 校验器能可靠执行跨字段条件，
但生成的 JSON Schema 未必能稳定向 LLM 表达条件要求。通用描述也没有说明生成合法提案
所需的业务事实。

这是 schema、工具说明和运行时恢复之间的设计缺口，不是 provider 内部推理的证据。

## 4. 实现与测试范围
重点检查并按需修改：
- `src/jobs_status_manager/application_core/domain.py`
- `src/jobs_status_manager/agent/write_contracts.py`
- `src/jobs_status_manager/agent/tools.py`
- `src/jobs_status_manager/agent/turn_runtime.py`
- `src/jobs_status_manager/agent/tool_runtime.py`
- `src/jobs_status_manager/agent/write_proposals.py`
- `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py`

重点测试位置：
- `tests/unit/test_application_core_domain.py`
- `tests/integration/test_application_core.py`
- `tests/integration/test_conversation_agent.py`
- `tests/unit/test_openai_compatible_llm.py`

只修改满足验收行为所需的文件，不要重写无关会话、工具或领域代码。

## 5. 强制功能要求
### 5.1 写入事实来源
写入提案中的公司、部门、岗位、状态和面试轮次，只能来自当前用户消息，或无歧义的
持久化会话、application 上下文。

系统不得猜测或自动补全这些值。上下文存在多个候选项时必须询问用户，不能任选一个。

### 5.2 缺失或歧义字段
缺失或歧义字段必须产生自然语言澄清，并满足：

- 不创建 `PendingAction`；
- 不修改 application 或其他业务数据；
- 不发送确认提案；
- 当前运行被视为已处理，而不是 `FAILED`；
- 回复明确说明需要补充什么。

具体行为：

- `status=INTERVIEW` 且轮次缺失时，明确询问面试轮次；
- 岗位缺失时询问岗位，不得选择默认岗位；
- 状态缺失时询问状态，不得选择默认状态；
- 多字段缺失时可一次询问全部必要字段；
- 澄清问题不得泄漏内部 schema 或校验结构。

### 5.3 确认门禁与领域规则
所有合法写入继续使用现有确认门禁。合法参数只能创建确认提案或现有等价待确认对象，
确认前不得执行业务写入。

不得放宽领域规则：

- `INTERVIEW` 仍然必须有正数面试轮次；
- 非 `INTERVIEW` 状态仍然禁止面试轮次；
- 非法状态枚举仍然必须被拒绝；
- 必填字段仍然必须满足领域契约。

### 5.4 有界恢复

malformed tool output 必须通过有界机制恢复，结果可以是用户澄清，也可以是一次或固定
次数的 LLM 纠正机会，但必须满足：

- 不进行无界重试；
- 不循环调用同一无效工具；
- 不自动填充缺失业务值；
- 达到上限后安全结束并给出可理解回复；
- 恢复上限由测试明确固定。

### 5.5 安全诊断

用户回复不得暴露内部模型名、schema、字段堆栈、provider payload 或原始
`ValidationError`。

内部诊断必须保留安全的结构化信息，至少包括工具名称、校验位置、校验类型、脱敏原因，
以及恢复尝试次数或是否达到上限。

不得记录完整用户消息、完整 provider payload、密钥、Authorization header、用户标识、
未脱敏参数或不必要的完整工具结果。

## 6. 可选实现方案

实现者可以单独或组合采用以下方案：

- 调整 schema 或参数模型，让条件要求更易被工具调用方理解；
- 改进工具描述或 system prompt，明确必需事实和禁止猜测规则；
- 在工具调用前识别缺失字段并直接澄清；
- 增加有上限的 tool error 到 LLM 纠正回合；
- 把 Pydantic 错误映射为脱敏的结构化恢复信号；
- 在运行时区分可恢复参数错误与系统错误。

这些方案不是强制架构。实现应采用满足外部行为的最小一致改动，不要为了某个方案增加
无关抽象。

## 7. TDD 要求

必须先添加失败测试，再实现修复。测试不得依赖真实 provider、生产数据库或外部 QQ。

### 7.1 必测场景

1. 精确生产回归：参数语义等于
   `{"company":"小米","position":"软件研发工程师","status":"INTERVIEW"}`，结果应询问
   面试轮次，不创建 `PendingAction`，不写业务数据，运行不以 `FAILED` 结束；
2. 缺少 `position` 时询问岗位，不选择默认值；
3. 缺少 `status` 时询问状态，不选择默认值；
4. `INTERVIEW` 缺少 `interview_round` 时明确询问轮次；
5. 非 `INTERVIEW` 带 `interview_round` 时拒绝该组合；
6. 非法状态枚举被拒绝，不产生业务写入；
7. 合法 `INTERVIEW` 加正数轮次时只创建确认提案；
8. 合法非面试状态且无轮次时只创建确认提案；
9. 任何合法写入在确认前都不修改业务数据；
10. malformed tool output 能有界恢复，达到上限后不再调用或补值；
11. 用户回复不含原始错误或敏感值；
12. 内部诊断保留安全的 validation location、type 和 reason。

### 7.2 回归保护

测试必须确认以下路径无回归：

- `AddKnowledge`；
- `RemoveKnowledge`；
- read tools；
- tool continuation；
- delivery；
- 既有确认、取消、过期和恢复行为。

测试应断言可观察行为、持久化结果和调用次数，不应只断言辅助函数被调用。

## 8. 可观测性要求

可增加脱敏结构化字段，例如 `run_id`、内部 `tool_call_id`、`tool_name`、`sequence`、
validation location、validation type、safe reason code、recovery attempt、recovery outcome、
是否创建确认提案。

共享日志禁止包含完整 prompt、完整 provider 请求或响应、完整 provider call ID、密钥、
令牌、Authorization header、未脱敏工具参数、用户 OpenID 或其他用户标识。

日志应能区分需要用户补充信息、有界纠正成功、达到恢复上限和内部系统错误。

## 9. 非目标

本修复不包括：

- 修改 QQ ingress、webhook、鉴权、SDK 或 transport；
- 绕过或删除确认门禁；
- 静默设置 `interview_round=1`；
- 放宽或删除领域校验；
- 把更换 provider 或模型作为主要修复；
- 通过生产 SQL 手工写入或修正业务数据；
- 删除、重建或重置数据库；
- 无界重试、默认补值或大范围无关重构。

若观察到约 60 秒延迟，应作为独立问题调查，不能归入本缺陷的已确认根因。

## 10. 验收标准

- 精确生产回归不再导致终止性 `AgentRun` 失败；
- 缺少轮次、岗位或状态时，用户收到明确澄清；
- 缺失或歧义字段不创建 `PendingAction`，也不修改业务数据；
- 合法写入只创建确认提案，确认前没有 business mutation；
- `INTERVIEW`、非 `INTERVIEW` 和枚举规则保持严格；
- malformed tool output 的恢复次数有明确上限；
- 用户侧无敏感错误泄漏，内部保留脱敏校验位置、类型和原因；
- `AddKnowledge`、`RemoveKnowledge`、read tools、tool continuation 和 delivery 无回归；
- 目标测试、完整 pytest、Ruff、basedpyright、Alembic 和 diff 检查通过。

## 11. 建议验证命令

以下命令供实现修复时使用。本次仅创建需求文档，没有声称执行这些命令。

```bash
uv run pytest -q tests/unit/test_application_core_domain.py
uv run pytest -q tests/integration/test_application_core.py
uv run pytest -q tests/integration/test_conversation_agent.py
uv run pytest -q tests/unit/test_openai_compatible_llm.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic check
git diff --check
```

## 12. 受控 Smoke 与证据

在隔离环境使用测试账号和可清理数据执行：

1. 发送仅表达公司写入意图、但缺少岗位和状态的消息；
2. 确认系统询问缺失字段，且没有 `PendingAction` 和业务写入；
3. 补充岗位与 `INTERVIEW`，但不提供轮次；
4. 确认系统明确询问轮次，运行结果不是 `FAILED`；
5. 补充正数轮次，确认只生成一次确认提案；
6. 执行确认，确认业务写入、最终回复和 delivery 各只发生一次。

应保留脱敏的运行状态、工具名、sequence、内部关联 ID、`PendingAction` 状态、确认前后
业务差异、恢复次数和 delivery 次数。

共享证据不得暴露 prompt、provider payload、完整 provider call ID、密钥、令牌、用户
标识或未脱敏业务值。敏感原始证据如确需留存，应进入现有受限证据存储，不得提交到
仓库或普通日志。
