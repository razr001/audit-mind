# System Agent 代码阅读指南

## 从哪里开始

先阅读 `services/system_agent_service.py`。它是总入口，负责把一条用户消息组织成下面的流程：

```text
用户消息
  ↓
输入安全护栏
  ↓
capability_router.py 判断意图
  ↓
tool_registry.py 裁剪本轮可用工具
  ↓
runner.py 创建并运行 LangGraph Agent
  ├─ 只读工具：直接执行并返回结果
  └─ 写工具：暂停 → 用户批准/拒绝 → 从 checkpoint 恢复
```

推荐阅读顺序：

1. `services/system_agent_service.py`：主流程和恢复流程。
2. `runner.py`：模型、调用上限、人工审批和 checkpoint 的装配。
3. `capability_router.py`：用户请求如何被分成问答、起草、系统读取和系统写入。
4. `tool_registry.py`：每类意图允许模型看到哪些工具。
5. `services/system_agent_read_tools.py`：只读工具。
6. `services/system_agent_write_tools.py`：写工具适配层。
7. `services/agent_tool_execution_service.py`：批准后的幂等执行围栏。
8. `services/system_agent_state_service.py`：数据库状态如何一起变化。
9. `services/system_agent_dependency.py`：FastAPI 依赖装配；核心流程不放在这里。

## 当前能力边界

- 只读：法规搜索、列表、详情、规则、解析块和短期下载地址；文档列表、详情和
  短期下载地址；审计任务列表、详情和分页结果。
- 写入：文本新增法规、重新处理法规、从 Markdown 或已有文档创建审计任务、
  重试审计任务、启动或同步文档解析。所有写入都必须人工确认。
- 合同起草和审查属于内容生成，不是系统写操作；仍会经过输入、输出和引用护栏。
- 删除操作暂不向 Agent 开放。二进制文件仍先走现有上传接口；上传完成后，Agent
  使用服务端生成的 `document_id` 继续解析或创建审计任务。聊天请求目前只接收文本，
  因此不要增加“本地路径”或“任意 URL 上传”工具来绕过上传校验。

## 核心变量命名

阅读调用链时，可以按下面的名字判断对象职责：

| 变量名 | 实际职责 |
| --- | --- |
| `assistant_service` | 管理会话和聊天消息，不执行 Agent 推理 |
| `answer_service` | 对话 API 中的 `SystemAgentService` 参数；调用 `stream()` 会直接跳到真实实现 |
| `system_agent_service` | System Agent 总编排服务 |
| `regulation_qa_service` | 旧的法规检索、引用核验和安全护栏服务 |
| `state_service` | 维护 Agent Run、Action 和 AI 消息的事务状态 |
| `agent_run` | 一次用户消息对应的 Agent 执行记录 |
| `runtime_context` | 服务端注入的可信用户、会话、Run 和请求身份 |
| `available_tools` | 经过意图裁剪后，本轮允许模型调用的工具 |
| `agent_result` | LangGraph 执行返回的完整结果 |
| `final_output` | 从 Agent 结果中提取并经过护栏校验的最终回答 |
| `tool_receipt` | 写工具落库后产生的可信执行凭证 |

`qa_service` 不再用于 Assistant 对话 API 或 System Agent 编排层。只有明确属于法规
问答的依赖才使用完整名称 `regulation_qa_service`，避免把旧 QA 流程误认为 System Agent。

## 四个常见 ID

```text
conversation_id                    一整段对话
  └─ assistant_message_id          聊天界面中的一条 AI 回复
       └─ run_id                   这条回复对应的一次 Agent 执行
            ├─ action_id           等待用户批准的一次写操作
            └─ tool_call_id        模型生成的一次工具调用
```

- `assistant_message_id` 决定进度和最终结果更新到聊天记录中的哪条 AI 消息。
- `run_id` 用于关联本次执行的状态、调用次数、Action 和工具凭证。
- `action_id` 是前端批准或拒绝时提交的业务 ID。
- `tool_call_id` 由模型运行时生成，用来确保批准的动作和实际工具调用是同一个。

另外还有 `thread_id`：它是 LangGraph checkpoint 的恢复键。用户批准后必须使用原
`thread_id`，才能从暂停位置继续执行，而不是重新开始一轮 Agent。

## 为什么 Action 和 ToolCall 要分开

`AssistantAction` 记录“用户批准了什么”，包括工具名、完整参数、参数摘要和决定状态。

`AssistantToolCall` 记录“系统实际上执行了什么”，包括幂等键、执行状态、结果资源和结果码。

两者分开后，可以识别以下异常：

- 用户批准后的参数被模型修改；
- 客户端断线时工具其实已经成功；
- 工具执行中断，无法判断副作用是否发生；
- 迟到的执行结果试图覆盖人工对账结论。

## 写操作为什么会暂停两次运行之间

第一次运行中，模型选择写工具后，`HumanInTheLoopMiddleware` 会在工具真正执行前暂停。
`system_agent_approval_service.py` 把暂停内容保存成 `PENDING Action`，并通知前端确认。

用户批准后，`resume_stream()` 执行下面的步骤：

1. 把 Action 从 `APPROVED` 推进到 `EXECUTING`。
2. 使用原 `thread_id` 从 LangGraph checkpoint 恢复。
3. 写工具经过 `AgentToolExecutionService` 校验 Action 和参数摘要。
4. 现有法规或审计 Service 执行真正业务操作。
5. 工具凭证、Action、Run 和 AI 消息一起提交最终状态。

## 修改代码时遵守的边界

- 新增只读能力：优先在 `system_agent_read_tools.py` 包装现有查询 Service。
- 新增写能力：先建立命令 Service，再从 `system_agent_write_tools.py` 接入。
- 新写工具必须加入 `runner.py` 的 `WRITE_TOOL_NAMES`，否则不会触发人工审批。
- 同时在 `tool_registry.py` 中声明哪些意图可以使用该工具。
- 不要允许模型传入 `user_id`、`conversation_id` 或 `run_id`，这些值只能来自 `AgentRuntimeContext`。
- 不要绕过 `AgentToolExecutionService` 直接执行写操作。
- 中断后结果不确定时不要自动重试，应进入 `RECONCILIATION_REQUIRED`。

## 修改后的必跑检查

```text
uv run pyright --project pyrightconfig.json
uv run ruff check app test
uv run pytest
```

Pyright 配置至少覆盖整个 Agent 目录、对话 API、流式协议、AssistantService 和会话缓存。
这些模块之间的接口签名不兼容会直接让架构测试失败，不再依赖 IDE 或人工逐行发现。
