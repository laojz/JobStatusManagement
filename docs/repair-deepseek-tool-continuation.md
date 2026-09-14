# DeepSeek 工具调用续答修复需求

## 1. 目的

修复 QQ 普通对话在第一次 DeepSeek 请求成功、执行工具成功后，第二次
DeepSeek 续答请求返回 `400 invalid_request_error`，导致机器人不发送最终回复的问题。

本文件用于直接交给另一个 coding agent 执行。除非本文件明确要求，修复 agent
不得扩大修改范围。

## 2. 生产证据

生产环境在 2026-09-14 的运行记录如下：

```text
QQ callback                         HTTP 200
DeepSeek 第一次 conversation 请求    HTTP 200
工具调用                            SearchApplications({})
工具结果                            data=[]，tool_error 为空
DeepSeek 第二次 conversation 请求    HTTP 400
AgentRun                            FAILED
error                               request_kind=conversation
                                    http_status=400
                                    provider_code=invalid_request_error
delivery_state                      空
```

对应数据库记录：

```text
run_id:        3586c8a9-fc8c-4c26-bf58-388623b27f97
tool_call_id:  888a68e9-d362-4b23-8e60-3126734bbe3c
tool_name:     SearchApplications
arguments:     {}
tool_result:   []
tool_error:    NULL
```

数据库时间为 UTC；日志时间为 CST。该证据表明 QQ 入站、LLM 首次请求、工具执行
和工具结果持久化均已成功，失败发生在工具结果提交后的 LLM 续答阶段。

不得在实现、测试或文档中臆造未捕获的 DeepSeek `error.message`。目前唯一已知的
供应商错误信息是 HTTP 400、`invalid_request_error`。

## 3. 当前根因

相关代码主要位于：

- `src/jobs_status_manager/agent/contracts.py`
- `src/jobs_status_manager/agent/turn_runtime.py`
- `src/jobs_status_manager/agent/runtime_support.py`
- `src/jobs_status_manager/agent/tool_runtime.py`
- `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py`
- `tests/unit/test_openai_compatible_llm.py`

当前流程只保留了工具名称和参数，丢失了供应商返回的原始 tool-call ID 以及
assistant 的完整 `tool_calls` 消息。随后请求构造器直接追加 `role=tool` 消息。

OpenAI-compatible Chat Completions 工具调用续答必须保留如下关系：

```text
assistant(tool_calls=[provider_call_id, function name, arguments])
tool(tool_call_id=同一个 provider_call_id, content=tool result)
```

不能只发送：

```text
user
tool(tool_call_id=内部数据库 UUID, content=tool result)
```

当前 `ToolCall.id` 是应用内部数据库 ID，不能默认作为供应商的
`tool_call_id`。两者必须作为不同概念建模和持久化。

## 4. 修复目标

修复后，一次需要工具的对话必须支持以下完整流程：

```text
QQ 入站
-> 第一次 LLM 请求
-> LLM 返回 assistant tool_calls
-> 持久化供应商 tool-call 元数据
-> 执行工具
-> 持久化工具结果
-> 第二次 LLM 请求，发送完整 assistant/tool 配对
-> LLM 返回最终 answer
-> 现有 QQ delivery 流程发送一次
-> AgentRun COMPLETED
```

其中工具结果为 `[]` 时仍然是成功结果，不得转换成 `NULL`、缺失字段或错误。

## 5. 功能要求

### 5.1 供应商工具调用身份

扩展现有类型和持久化模型，使系统能够保存并恢复至少以下信息：

- 供应商原始 tool-call ID；
- tool-call 类型，通常为 `function`；
- 函数名称；
- 函数参数的 JSON 表示，或与原始表示语义等价且可稳定重建的表示；
- 工具调用在当前 AgentRun 中的顺序；
- 内部数据库 `ToolCall.id` 与供应商 tool-call ID 的独立关系。

供应商 ID 必须从 DeepSeek 响应解析开始，一直传递到第二次请求。不得在解析
响应时丢弃该 ID，也不得使用新生成的内部 UUID 替代它。

### 5.2 assistant/tool 消息重建

`OpenAICompatibleLLM` 的 conversation payload 必须重建合法、有序的消息序列。
对单个工具调用，至少应等价于：

```json
{
  "role": "assistant",
  "tool_calls": [
    {
      "id": "provider-call-id",
      "type": "function",
      "function": {
        "name": "SearchApplications",
        "arguments": "{}"
      }
    }
  ]
}
```

紧接着发送：

```json
{
  "role": "tool",
  "tool_call_id": "provider-call-id",
  "content": "[]"
}
```

要求：

- 每个 `role=tool` 前面必须存在包含对应 `tool_calls` 的 assistant 消息；
- `tool.tool_call_id` 必须严格等于 assistant `tool_calls[].id`；
- 多个工具调用必须保留供应商返回顺序，并逐一配对；
- 工具结果内容必须保留原始有效内容，包括空数组 `[]`；
- 不能静默丢弃第二个及后续工具调用；
- 不能依赖进程内内存才能完成续答；
- 进程重启后必须能够从数据库重建同一消息序列。

### 5.3 运行时和恢复

更新 `run_turns()`、`load_context()` 及相关持久化逻辑，使其满足：

- 第一次 LLM 返回工具调用后，在执行工具前保存恢复所需的供应商元数据；
- 工具结果和对应的供应商 ID 关联保存；
- 已完成工具在重启恢复时不得重复执行；
- 工具结果已存在但最终 LLM 请求尚未完成时，重启后应继续请求而不是重新执行工具；
- 缺少供应商 ID 的旧数据不得伪造 ID；
- 无法安全重建的旧运行必须进入现有安全失败路径，不得发出非法请求；
- 最终回答仍使用现有 AgentRun 完成和 QQ delivery 流程，且只发送一次；
- 现有确认型写操作、挂起恢复和邮件分析流程不得被破坏。

如果新增数据库字段，必须遵循现有 Alembic migration 约定，并覆盖升级、降级和
现有数据行为。不得通过删除数据库或重置生产数据解决问题。

## 6. 测试要求

必须先增加失败测试，再实现修复。

### 6.1 Adapter 单元测试

在 `tests/unit/test_openai_compatible_llm.py` 或项目约定的相邻测试文件中覆盖：

1. 解析工具调用时保留供应商原始 ID；
2. 单工具调用的 payload 包含 assistant `tool_calls`，然后才是 matching tool 消息；
3. `tool` 消息的 ID 等于供应商 ID，而不是内部数据库 UUID；
4. `data=[]` 被序列化为合法工具内容；
5. 工具结果后返回最终 answer；
6. 多工具调用保持顺序并逐一匹配 ID；
7. 缺失 ID、重复 ID、未知 result ID 或无法重建 assistant 消息时安全失败；
8. 现有 mail analysis 行为保持不变；
9. 错误和诊断中不包含 API Key、Authorization header、完整 prompt、敏感参数或完整工具结果。

测试应断言完整 `messages` 结构，而不是只断言最后一条消息的 role。

### 6.2 Runtime/持久化集成测试

至少覆盖：

1. `SearchApplications({}) -> [] -> final answer` 的完整成功路径；
2. 两个或更多工具调用的有序续答路径；
3. 工具结果持久化后重新加载 context，再继续 LLM 请求；
4. 重启/重新加载后不重复执行已完成工具；
5. 最终回答持久化并只发送一次；
6. 缺少旧供应商元数据时不伪造 ID、不发送非法续答请求；
7. 现有确认型写工具和 delivery 相关测试继续通过。

## 7. 可观测性要求

可以增加结构化、脱敏的诊断字段，例如：

- `run_id`；
- request kind；
- continuation sequence；
- tool-call 数量；
- 是否存在 assistant tool-call 元数据；
- provider ID 是否与 tool result 匹配；
- 工具结果是否为空；
- HTTP 状态码和已安全解析的 provider code。

禁止记录：

- API Key 或 Authorization header；
- 完整用户 prompt；
- 未脱敏的工具参数；
- 完整工具结果；
- 完整供应商响应体；
- 不必要的用户 OpenID。

供应商错误响应体如需临时诊断，也必须使用脱敏字段，并在代码中保持现有安全错误
边界；不得为了排查问题永久打印敏感请求或响应。

## 8. 非目标

本修复不包括：

- 修改 QQ webhook、QQ 鉴权或 QQ SDK 发送逻辑；
- 更换 `deepseek-flash` 模型作为首要解决方案；
- 增加备用供应商、重试循环或 prompt 修复 fallback；
- 放宽工具调用消息校验；
- 删除或重建生产数据库；
- 重写无关的会话历史、邮件分析或业务工具；
- 修改现有确认、权限和文件上传安全边界。

## 9. 验收标准

修复 agent 完成后必须满足：

- `SearchApplications({})` 返回空数组时，QQ 用户仍能收到正常最终回复；
- 第二次 DeepSeek 请求的 payload 中存在正确的 assistant `tool_calls`；
- 每个 tool 消息的 `tool_call_id` 与对应 assistant tool call ID 完全一致；
- 内部数据库 ID 与供应商 tool-call ID 不再混用；
- 单工具、多工具、最终回答和重启恢复测试全部通过；
- 既有测试、Ruff、basedpyright 和 Alembic 检查通过；
- 不产生 API Key、用户隐私或完整 provider payload 泄漏；
- 现有 QQ delivery、确认型写操作和邮件分析行为无回归。

建议验证命令：

```bash
uv run pytest -q tests/unit/test_openai_compatible_llm.py
uv run pytest -q tests/integration
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run alembic check
```

最后应在受控环境执行一次真实 smoke：发送普通文本，确认日志出现第一次成功请求、
工具执行、第二次成功请求和一次 QQ 最终回复。生产日志中不得包含密钥或完整请求内容。
