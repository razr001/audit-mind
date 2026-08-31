import json
from datetime import datetime
from uuid import UUID

from app.core.config import get_settings
from app.core.logger import logger
from app.infrastructure.redis_client import RedisClient, redis_client
from app.models.assistant import AssistantConversation

settings = get_settings()


class AssistantConversationCache:
    """Redis 中的会话只读快照；缓存故障时回退数据库，不影响核心对话流程。"""

    def __init__(
        self,
        client: RedisClient = redis_client,
        ttl_seconds: int = settings.ASSISTANT_CONVERSATION_CACHE_TTL_SECONDS,
    ) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    async def get(
        self, conversation_id: UUID, user_id: UUID
    ) -> AssistantConversation | None:
        try:
            raw = await self.client.client.get(self._key(conversation_id, user_id))
        except Exception as exc:
            self._log_failure("get", conversation_id, exc)
            return None
        if raw is None:
            return None
        try:
            return self._deserialize(json.loads(raw))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._log_failure("decode", conversation_id, exc)
            await self.invalidate(conversation_id, user_id)
            return None

    async def set(self, conversation: AssistantConversation) -> None:
        payload = json.dumps(
            self._serialize(conversation),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self.client.client.set(
                self._key(conversation.id, conversation.user_id),
                payload,
                ex=self.ttl_seconds,
            )
        except Exception as exc:
            self._log_failure("set", conversation.id, exc)

    async def get_list(
        self, *, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[AssistantConversation], int] | None:
        """读取某个用户的一页会话；版本变化后旧分页不会再被命中。"""

        try:
            version = await self._list_version(user_id)
            key = self._list_key(user_id, version, offset, limit)
            raw = await self.client.client.get(key)
        except Exception as exc:
            self._log_failure("list_get", user_id, exc)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            items = [self._deserialize(item) for item in payload["items"]]
            total = int(payload["total"])
            if total < 0 or any(item.user_id != user_id for item in items):
                raise ValueError("conversation list cache ownership mismatch")
            return items, total
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._log_failure("list_decode", user_id, exc)
            try:
                await self.client.client.delete(key)
            except Exception as delete_exc:
                self._log_failure("list_delete", user_id, delete_exc)
            return None

    async def set_list(
        self,
        *,
        user_id: UUID,
        offset: int,
        limit: int,
        items: list[AssistantConversation],
        total: int,
    ) -> None:
        try:
            version = await self._list_version(user_id)
            payload = json.dumps(
                {"items": [self._serialize(item) for item in items], "total": total},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self.client.client.set(
                self._list_key(user_id, version, offset, limit),
                payload,
                ex=self.ttl_seconds,
            )
        except Exception as exc:
            self._log_failure("list_set", user_id, exc)

    async def invalidate_list(self, user_id: UUID) -> None:
        """推进用户列表版本；旧分页键等待 TTL 自动回收，无需扫描删除。"""

        try:
            key = self._list_version_key(user_id)
            await self.client.client.incr(key)
            await self.client.client.expire(key, max(60, self.ttl_seconds * 2))
        except Exception as exc:
            self._log_failure("list_invalidate", user_id, exc)

    async def invalidate(self, conversation_id: UUID, user_id: UUID) -> None:
        try:
            await self.client.client.delete(self._key(conversation_id, user_id))
        except Exception as exc:
            self._log_failure("delete", conversation_id, exc)

    @staticmethod
    def _key(conversation_id: UUID, user_id: UUID) -> str:
        # v1 允许未来调整 JSON 结构时直接切换命名空间，而不必扫描旧键。
        # user_id 进入键名，未授权用户无法命中或驱逐其他用户的会话缓存。
        return f"assistant:conversation:v1:{user_id}:{conversation_id}"

    async def _list_version(self, user_id: UUID) -> int:
        value = await self.client.client.get(self._list_version_key(user_id))
        return int(value) if value is not None else 0

    @staticmethod
    def _list_version_key(user_id: UUID) -> str:
        return f"assistant:conversation-list:v1:{user_id}:version"

    @staticmethod
    def _list_key(user_id: UUID, version: int, offset: int, limit: int) -> str:
        return f"assistant:conversation-list:v1:{user_id}:{version}:{offset}:{limit}"

    @classmethod
    def _serialize(cls, conversation: AssistantConversation) -> dict[str, str | None]:
        return {
            "id": str(conversation.id),
            "user_id": str(conversation.user_id),
            "title": conversation.title,
            "last_message_at": cls._format_datetime(conversation.last_message_at),
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
        }

    @classmethod
    def _deserialize(cls, payload: dict) -> AssistantConversation:
        return AssistantConversation(
            id=UUID(payload["id"]),
            user_id=UUID(payload["user_id"]),
            title=payload["title"],
            last_message_at=cls._parse_datetime(payload["last_message_at"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )

    @staticmethod
    def _format_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None

    @staticmethod
    def _log_failure(operation: str, conversation_id: UUID, exc: Exception) -> None:
        logger.warning(
            "assistant.conversation_cache.failed",
            operation=operation,
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
        )


assistant_conversation_cache = AssistantConversationCache()
