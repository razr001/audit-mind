from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AgentRuntimeContext:
    """服务端注入的可信运行上下文，模型只能读取，不能通过工具参数覆盖。

    conversation_id 表示整段会话；run_id 表示本次 Agent 执行；request_id 用于
    串联日志和业务操作。聊天中的 AI 消息 ID 存在 AssistantAgentRun 上。
    """

    user_id: UUID
    conversation_id: UUID
    run_id: UUID
    request_id: str | None
    permissions: frozenset[str] = frozenset()
