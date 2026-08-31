import hashlib
from datetime import datetime, timezone
from uuid import UUID

from app.infrastructure.redis_client import RedisClient, redis_client

_ROTATE_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('del', KEYS[1])
redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
redis.call('sadd', KEYS[2], ARGV[4])
redis.call('expire', KEYS[2], ARGV[3])
return 1
"""

_DELETE_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('del', KEYS[1])
redis.call('srem', KEYS[2], ARGV[2])
return 1
"""


class RefreshTokenStore:
    """在 Redis 保存一次性 Refresh Token 摘要并提供原子轮换。"""

    def __init__(self, client: RedisClient = redis_client) -> None:
        self.client = client

    async def store(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        token: str,
        expires_at: datetime,
    ) -> None:
        ttl_seconds = self._ttl_seconds(expires_at)
        token_key = self._token_key(session_id)
        user_key = self._user_key(user_id)
        async with self.client.client.pipeline(transaction=True) as pipeline:
            pipeline.set(token_key, self._token_hash(token), ex=ttl_seconds)
            pipeline.sadd(user_key, str(session_id))
            pipeline.expire(user_key, ttl_seconds)
            await pipeline.execute()

    async def rotate(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        current_token: str,
        rotated_token: str,
        expires_at: datetime,
    ) -> bool:
        """消费旧 token 并写入新 token；并发刷新只有一个调用能成功。"""
        result = await self.client.client.eval(
            _ROTATE_SCRIPT,
            2,
            self._token_key(session_id),
            self._user_key(user_id),
            self._token_hash(current_token),
            self._token_hash(rotated_token),
            self._ttl_seconds(expires_at),
            str(session_id),
        )
        return bool(result)

    async def revoke(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        token: str,
    ) -> None:
        await self.client.client.eval(
            _DELETE_SCRIPT,
            2,
            self._token_key(session_id),
            self._user_key(user_id),
            self._token_hash(token),
            str(session_id),
        )

    async def revoke_user(self, user_id: UUID) -> None:
        """密码修改或用户删除时清除该用户的全部 Refresh Token。"""
        user_key = self._user_key(user_id)
        session_ids = await self.client.client.smembers(user_key)
        async with self.client.client.pipeline(transaction=True) as pipeline:
            for session_id in session_ids:
                pipeline.delete(f"auth:refresh:{session_id}")
            pipeline.delete(user_key)
            await pipeline.execute()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _ttl_seconds(expires_at: datetime) -> int:
        return max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))

    @staticmethod
    def _token_key(session_id: UUID) -> str:
        return f"auth:refresh:{session_id}"

    @staticmethod
    def _user_key(user_id: UUID) -> str:
        return f"auth:refresh:user:{user_id}"


refresh_token_store = RefreshTokenStore()
