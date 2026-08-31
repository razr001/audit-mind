import asyncio

import aiohttp


class AsyncHttpClient:
    """应用级通用异步 HTTP 连接池，由 lifespan 统一释放。"""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        """延迟创建 Session，确保它绑定到实际运行应用的事件循环。"""
        session = self._session
        if session is not None and not session.closed:
            return session

        async with self._session_lock:
            session = self._session
            if session is None or session.closed:
                session = aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar(),
                )
                self._session = session
            return session

    async def close(self) -> None:
        """关闭连接池；未创建或重复关闭时保持幂等。"""
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()


outbound_http_client = AsyncHttpClient()
