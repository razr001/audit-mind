import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.asyncio_utils import await_cancellation_safe
from app.core.config import get_settings

AGENT_HEARTBEAT_SECONDS = 15.0
_agent_capacity = asyncio.Semaphore(get_settings().ASSISTANT_AGENT_MAX_CONCURRENT_RUNS)


@dataclass(frozen=True)
class CompletedAgentInvocation:
    result: dict[str, Any]


async def agent_heartbeat_events(
    task: asyncio.Future[Any],
) -> AsyncIterator[dict[str, Any]]:
    """等待 Agent Task，并在模型或工具静默期间持续产生 SSE 心跳。"""

    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=AGENT_HEARTBEAT_SECONDS)
        if task not in done:
            yield {"type": "heartbeat", "data": {}}


async def stop_agent_task(task: asyncio.Future[Any]) -> None:
    """消费 Agent Task 终态；断连时先取消并等待，禁止后台继续执行工具。"""

    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def run_agent_with_progress(
    invocation_factory: Callable[[], Awaitable[dict[str, Any]]],
    *,
    timeout_seconds: float | None = None,
) -> AsyncIterator[dict[str, Any] | CompletedAgentInvocation]:
    """限制进程内并发，并在排队和执行期间都持续发送心跳。"""

    capacity_task = asyncio.create_task(_agent_capacity.acquire())
    agent_task: asyncio.Future[dict[str, Any]] | None = None
    acquired = False
    result: dict[str, Any] | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            async for heartbeat in agent_heartbeat_events(capacity_task):
                yield heartbeat
            acquired = capacity_task.result()
            agent_task = asyncio.ensure_future(invocation_factory())
            async for heartbeat in agent_heartbeat_events(agent_task):
                yield heartbeat
            result = agent_task.result()
    finally:
        await await_cancellation_safe(stop_agent_task(capacity_task))
        # 容量可能在最后一个 heartbeat 已发出、生成器关闭前刚好获取成功。
        # 必须根据 Task 终态补记，否则会永久少一个并发槽位。
        if capacity_task.done() and not capacity_task.cancelled():
            acquired = bool(capacity_task.result()) or acquired
        if agent_task is not None:
            await await_cancellation_safe(stop_agent_task(agent_task))
        if acquired:
            _agent_capacity.release()
    if result is None:
        raise RuntimeError("agent invocation completed without a result")
    yield CompletedAgentInvocation(result)
