from collections.abc import Awaitable, Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.runner import create_system_agent
from app.infrastructure.redis_lock import RedisLease, run_with_lease_guard
from app.schemas.assistant import AssistantActionDecisionType


def create_initial_invocation(
    *,
    tools: Sequence[BaseTool],
    messages: list[dict[str, Any]],
    context: AgentRuntimeContext,
    thread_id: str,
) -> Awaitable[dict[str, Any]]:
    return create_system_agent(tools).ainvoke(
        {"messages": messages},
        context=context,
        config={"configurable": {"thread_id": thread_id}},
    )


def create_resume_invocation(
    *,
    tools: Sequence[BaseTool],
    decision: AssistantActionDecisionType,
    context: AgentRuntimeContext,
    thread_id: str,
    lease: RedisLease | None,
) -> Awaitable[dict[str, Any]]:
    invocation = create_system_agent(tools).ainvoke(
        # Command(resume=...) 会继续原中断点，而非重新问模型。
        Command(resume={"decisions": [{"type": decision.value.lower()}]}),
        context=context,
        config={"configurable": {"thread_id": thread_id}},
    )
    return run_with_lease_guard(lease, invocation) if lease is not None else invocation
