import sys
from typing import Any, cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings


class AgentCheckpointer:
    """Own the process-lifetime PostgreSQL connection pool used by LangGraph."""

    def __init__(self) -> None:
        # psycopg_pool and LangGraph expose incompatible invariant generic aliases
        # for the same runtime pool object, so keep the third-party boundary generic.
        self._pool: AsyncConnectionPool[Any] | None = None
        self._saver: AsyncPostgresSaver | None = None

    async def initialize(self) -> None:
        if self._saver is not None:
            return
        if sys.platform == "win32":
            import asyncio

            loop = asyncio.get_running_loop()
            if not isinstance(loop, asyncio.SelectorEventLoop):
                raise RuntimeError(
                    "The Agent PostgreSQL checkpointer requires a SelectorEventLoop on "
                    "Windows. Start the API with `uv run auditmind-api`."
                )
        url = get_settings().DATABASE_URL.replace("+asyncpg", "").replace("+psycopg", "")
        # from_conn_string() only creates one connection. AsyncPostgresSaver protects
        # that connection with one asyncio.Lock, which would serialize every Agent
        # request in this API process. A pool lets concurrent conversations acquire
        # independent PostgreSQL connections while preserving the saver API.
        pool = AsyncConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            await pool.open(wait=True)
            saver = AsyncPostgresSaver(cast(Any, pool))
            await saver.setup()
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        self._saver = saver

    def get(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError("agent checkpointer is not initialized")
        return self._saver

    async def delete_thread(self, thread_id: str) -> None:
        """Delete terminal-run checkpoints so checkpoint tables do not grow forever."""

        await self.get().adelete_thread(thread_id)

    async def close(self) -> None:
        pool = self._pool
        self._pool = None
        self._saver = None
        if pool is not None:
            await pool.close()


agent_checkpointer = AgentCheckpointer()
