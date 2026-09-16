# Agent 对话上下文隔离修复输入

## 1. 修复目标

修复 `agent_run` 在构造 LLM prompt 时的上下文边界问题，确保：

1. 新的 `agent_run` 不会因为历史 run 的工具调用或工具失败状态而重新执行旧工具。
2. 当前 run 的 prompt 只把当前用户消息作为当前指令发送一次。
3. 历史对话可以作为参考上下文保留，但不能混入当前 run 的工具续接状态。
4. 一个较新的用户消息不会污染仍在运行、重试或排队中的旧 run。
5. 简单消息（例如“你好”）不会因为历史任务文本而被错误地路由到工具调用。

本修复只处理 Agent prompt 的上下文隔离和消息边界，不改变工具业务逻辑、工具权限、确认码机制或数据库业务实体。

## 2. 当前实现

### 2.1 每次调用都会重新组装 messages

`process_run()` 加载一个持久化的 `AgentRun`，然后由 `run_turns()` 调用 LLM。LLM adapter 不使用 provider 侧会话记忆，而是每次构造新的 Chat Completions 请求。

主要路径：

```text
process_run
  -> load_context
  -> run_turns
  -> OpenAICompatibleLLM.converse
  -> _conversation_messages
  -> _request
```

相关代码：

- `src/jobs_status_manager/agent/runtime.py:87`
- `src/jobs_status_manager/agent/runtime_support.py:112`
- `src/jobs_status_manager/agent/turn_runtime.py:182`
- `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py:103`

### 2.2 当前 prompt 的消息来源

当前 `_conversation_messages()` 按以下顺序构造消息：

```text
system: 固定 SYSTEM_PROMPT
system: session_summary（如果非空）
system: active_application_id（如果非空）
system: active_mail_id（如果非空）
system: active_knowledge_document_id（如果非空）
user/assistant: recent_messages
user: user_message
assistant/tool: 当前 run 的 tool_calls 和 tool_results
```

实现位置：

- `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py:103-122`
- `src/jobs_status_manager/agent/contracts.py:233-247`

### 2.3 当前用户消息被重复发送

webhook 先把当前消息保存为 `ConversationMessage(role="user")`。随后 `load_context()` 查询整个 session 的最近消息，因此当前消息已经出现在 `recent_messages` 中。之后 adapter 又单独追加 `prompt.user_message`。

当前行为可能形成：

```text
user: 查询当前所有求职状态
user: 查询当前所有求职状态
```

相关代码：

- `src/jobs_status_manager/agent/webhook.py:135-149`
- `src/jobs_status_manager/agent/runtime_support.py:138-144`
- `src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py:116-121`

### 2.4 历史消息按 session 读取，没有当前 run 的时间边界

`load_context()` 按 `session_id` 查询消息，并只取最后 `MAX_RECENT_MESSAGES` 条（当前值为 20）。查询没有限制消息必须早于当前 run 的用户消息。

这意味着旧 run 在重试或继续执行时，可能读到旧 run 创建之后才到达的新用户消息。

可能的时序：

```text
1. 用户发送 A：“查询所有求职状态”
2. 创建 run-A，run-A 开始执行
3. run-A 尚未结束
4. 用户发送 B：“你好”
5. 创建 queued run-B
6. run-A 重试或继续执行
```

此时 run-A 的 `context.user_message` 仍然是 A，但 `recent_messages` 可能已经包含 B。这会造成旧 run 的当前任务和历史上下文不一致。

### 2.5 工具调用和工具失败按 run 隔离

工具调用和工具结果通过 `agent_run_id` 查询：

```python
select(ToolCall).where(ToolCall.agent_run_id == run_id)
select(ToolResult).join(ToolCall, ...).where(ToolCall.agent_run_id == run_id)
```

因此，正常情况下新的 run 不会直接加载旧 run 的：

- `ToolCall`
- `ToolResult`
- pending tool call
- 工具执行错误

相关代码：

- `src/jobs_status_manager/agent/continuation_state.py:39-53`
- `src/jobs_status_manager/agent/runtime_support.py:146-153`

如果当前 run 的 `ToolResult.error` 非空，当前 run 会在 `load_context()` 阶段失败，不会继续调用 LLM。这种失败状态不会自动复制到新的 run。

### 2.6 仍然存在历史文本诱导工具调用的风险

虽然旧 run 的结构化工具状态不会跨 run 继承，但历史用户消息和历史助手最终回复会作为普通 `ConversationMessage` 进入 `recent_messages`。

例如旧历史中可能有：

```text
user: 查询当前所有求职状态
assistant: 我将调用 SearchApplications 查询所有求职状态……
```

新消息为：

```text
user: 你好
```

LLM 仍可能看到旧任务文本，并在当前指令很短、历史上下文很长或模型判断不稳定时再次选择工具。这是“历史文本影响当前决策”，不是“旧工具调用被系统重新执行”。

## 3. 需要修复的问题

### P0：当前消息重复进入 prompt

当前消息必须只出现一次，并且必须作为当前 turn 的最后一个用户指令，除非该消息是工具续接协议要求的一部分。

修复时需要明确选择一种数据边界：

- `recent_messages` 只包含当前消息之前的历史，然后单独追加 `user_message`；或
- `recent_messages` 包含当前消息，但 adapter 不再追加 `user_message`。

推荐第一种：`recent_messages` 表示历史，`user_message` 表示当前指令，职责更清晰。

### P0：历史消息必须有当前 run 的边界

构造 `recent_messages` 时不能简单查询整个 session 的最后 20 条。至少需要保证：

```text
created_at < 当前 run 的 user message created_at
```

必要时使用稳定的 `(created_at, id)` 顺序作为游标边界，以处理相同时间戳。

这样可以避免：

- 旧 run 读到之后到达的新消息；
- queued run 的消息污染当前正在执行的 run；
- 重试时 prompt 与原始 user message 不一致。

### P1：结构化工具续接状态必须继续按 run 隔离

不要为了修复历史文本问题，把整个 session 的历史 `ToolCall` 或 `ToolResult` 添加到新 run。

正确边界应保持为：

```text
历史对话文本：可按明确规则选取
当前 run 的工具续接状态：只按当前 run_id 选取
```

旧 run 的失败 `ToolResult.error` 不应作为新 run 的 prompt 消息，也不应导致新 run 在 `load_context()` 阶段失败。

### P1：降低历史文本对简单当前消息的错误影响

在不改变现有工具能力的前提下，prompt 应明确区分：

```text
历史对话，仅供参考
当前用户消息，必须优先响应
```

不要把旧助手消息转换成新的 assistant tool call。只有当前 run 的结构化 `tool_calls` 和 `tool_results` 才能进入 OpenAI tool continuation 消息。

如果现有 `SYSTEM_PROMPT` 需要增强，要求至少包括：

- 优先响应当前用户消息；
- 不因历史消息中曾经调用过工具而重复调用；
- 只有当前请求确实需要且工具可用时才调用工具；
- 对“你好”等简单问候直接回答，不调用业务工具。

## 4. 目标 prompt 形态

对于已有历史的简单问候，目标形态应类似：

```text
system: 固定系统提示
system: 历史消息仅供参考；当前用户消息优先
assistant: 之前的最终回答（可选，受历史窗口限制）
user: 之前的问题（可选，受历史窗口限制）
user: 你好
```

不得出现：

```text
user: 你好
user: 你好
```

也不得因为旧 run 有工具记录而出现：

```text
assistant: 旧 run 的 tool_calls
tool: 旧 run 的 tool result
```

对于当前 run 的工具续接，目标形态应类似：

```text
system: ...
user/assistant: 明确截止当前 run 的历史消息
user: 当前问题
assistant: 当前 run 的 tool_calls
tool: 当前 run 的 tool result
```

## 5. 验收标准

### 5.1 Prompt 组装单元测试

补充或调整 `tests/unit/test_openai_compatible_llm.py`，至少验证：

1. `recent_messages` 中包含当前用户消息时，最终 provider payload 中当前消息不会重复。
2. 正常历史 user/assistant 消息仍按既定顺序保留。
3. 当前消息位于当前 prompt 的明确位置，并且内容只出现一次。
4. 当前 run 的 tool call/result 仍能按 OpenAI-compatible 格式正确重建。
5. 另一个 run 的 tool call/result 不会出现在当前 prompt。
6. 旧 run 的失败结果不会被 `ConversationPrompt` 接收或重建。

### 5.2 Run 上下文边界集成测试

补充或调整集成测试，至少覆盖：

1. session 中存在旧 run 的成功工具调用和助手长回复，新 run 发送“你好”时，新 run 不加载旧 run 的结构化工具调用和工具结果。
2. 旧 run 的 `ToolResult.error` 非空，新 run 仍可正常加载自己的 context。
3. 旧 run 创建后、新 run 创建前后，旧 run 重试时不会把新 run 的用户消息放入自己的 `recent_messages`。
4. 新 run 的当前消息只作为自己的 `user_message` 出现一次。
5. 旧 run 失败不会改变新 run 的状态，除非新 run 自身发生错误。

### 5.3 行为验收

使用 fake LLM 或 `MockTransport` 验证：

```text
历史：查询求职状态，并曾经调用 SearchApplications
当前：你好
```

预期：

- 新 run 至少收到当前消息“你好”；
- 当前消息不重复；
- 新 run 不携带旧 run 的 assistant tool call 或 tool result；
- fake LLM 不被迫执行 `SearchApplications`；
- 新 run 可以生成正常问候回复并完成。

另一个场景：

```text
旧 run：工具执行失败
新 run：你好
```

预期：

- 旧 run 可以保持 `FAILED`；
- 新 run 不因旧 run 的错误而在 `load_context()` 阶段失败；
- 新 run 不会自动执行旧工具；
- 新 run 正常完成。

## 6. 非目标和约束

修复 agent 不应：

- 删除或弱化现有工具调用测试；
- 把所有历史消息完全删除，除非测试证明这是必要的设计变更；
- 把旧 run 的工具状态复制到新 run；
- 添加没有现有数据或需求依据的 fallback、重试或兼容分支；
- 修改工具的业务查询、写入确认或权限逻辑；
- 修改数据库 schema，除非实现消息边界确实无法通过现有字段安全表达，并且先补充说明；
- 仅通过字符串过滤“SearchApplications”等工具名来规避问题；
- 只修复单元测试 payload，而不验证真实的 `load_context -> run_turns -> converse` 路径。

## 7. 相关代码索引

- 会话和 run 模型：`src/jobs_status_manager/agent/models.py:20-119`
- 当前消息入库和 run 创建：`src/jobs_status_manager/agent/webhook.py:116-220`
- context 加载：`src/jobs_status_manager/agent/runtime_support.py:112-191`
- 当前 run 工具状态加载：`src/jobs_status_manager/agent/continuation_state.py:39-115`
- 多轮 LLM/tool 执行：`src/jobs_status_manager/agent/turn_runtime.py:182-241`
- provider messages 组装：`src/jobs_status_manager/infrastructure/adapters/openai_compatible_llm.py:103-197`
- assistant 最终回复持久化：`src/jobs_status_manager/agent/delivery.py:178-221`

## 8. 完成定义

只有同时满足以下条件才算完成：

1. 当前消息不会重复进入 provider payload。
2. `recent_messages` 不包含当前 run 创建之后到达的消息。
3. 新 run 不会读取旧 run 的结构化工具调用、工具结果或工具错误。
4. 历史助手文本仍按明确窗口保留，并且不会被伪装成新的 tool continuation。
5. 上述单元测试和集成测试通过。
6. 变更文件通过 LSP/静态检查，相关测试通过。
7. 至少执行一次 fake LLM 或 `MockTransport` 的端到端 Agent 行为验证。
