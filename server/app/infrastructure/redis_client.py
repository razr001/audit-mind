import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()


class RedisClient:
    """维护应用级异步 Redis 客户端及其生命周期。"""

    def __init__(self):
        self.client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_keepalive=True,
            # 从连接池借出空闲连接时定期 PING，避免把已经失效的长连接
            # 直接交给分布式锁操作。PING 同样受 socket_timeout 约束。
            health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
        )

    async def ping(self):
        """供启动健康检查验证 Redis 连通性。"""
        return await self.client.ping()

    async def close(self):
        """应用退出时释放 Redis 连接池。"""
        await self.client.close()


redis_client: RedisClient = RedisClient()
