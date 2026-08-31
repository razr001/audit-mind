from app.ai.agent.checkpointer import agent_checkpointer
from app.core.logger import logger


async def delete_agent_checkpoint(thread_id: str) -> None:
    """尽力清理终态 checkpoint，但不把已完成业务操作改判为失败。"""

    try:
        await agent_checkpointer.delete_thread(thread_id)
    except Exception as exc:
        logger.warning(
            "assistant.agent.checkpoint_cleanup_failed",
            thread_id=thread_id,
            error_type=type(exc).__name__,
        )
