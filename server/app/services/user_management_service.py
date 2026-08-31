from uuid import UUID

from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.error_codes import (
    CANNOT_DELETE_CURRENT_USER,
    INVALID_USER_INPUT,
    USER_NOT_FOUND,
    USERNAME_ALREADY_EXISTS,
)
from app.core.exceptions import BusinessException
from app.core.passwords import hash_password
from app.infrastructure.db.unit_of_work import UnitOfWork
from app.infrastructure.redis_client import RedisClient, redis_client
from app.infrastructure.redis_lock import acquire_redis_lease
from app.infrastructure.refresh_token_store import RefreshTokenStore
from app.models.user import User
from app.repositories.user_repository import UserRepository


class UserManagementService:
    """管理登录用户；当前系统尚未引入角色，调用入口必须要求已登录。"""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        repository: UserRepository,
        refresh_store: RefreshTokenStore,
        redis: RedisClient = redis_client,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.refresh_store = refresh_store
        self.redis = redis

    async def create(self, username: str, password: str) -> User:
        normalized = self._normalize_username(username)
        validated_password = self._validate_password(password)
        # Argon2 故意消耗 CPU，放入线程池避免阻塞 FastAPI 事件循环。
        password_hash = await run_in_threadpool(hash_password, validated_password)
        # 数据库按项目约定不增加业务唯一约束，因此用用户名粒度的 Redis
        # 锁串行化“检查并创建”，防止并发请求生成同名用户。
        async with acquire_redis_lease(
            key=f"lock:user:create:{normalized}",
            ttl_seconds=10,
            client=self.redis,
        ) as acquired:
            if not acquired:
                raise BusinessException(
                    USERNAME_ALREADY_EXISTS,
                    "username creation is already in progress",
                )
            try:
                async with self.uow:
                    if await self.repository.find_by_username(normalized) is not None:
                        raise BusinessException(
                            USERNAME_ALREADY_EXISTS,
                            "username already exists",
                        )
                    return await self.repository.save(
                        User(username=normalized, password_hash=password_hash)
                    )
            except IntegrityError as exc:
                # 数据库唯一索引是最终一致性边界。即使 Redis 锁失效或未来
                # 出现新的写入路径，接口也返回稳定业务错误而不是 500。
                raise BusinessException(
                    USERNAME_ALREADY_EXISTS,
                    "username already exists",
                ) from exc

    async def change_password(self, username: str, password: str) -> User:
        normalized = self._normalize_username(username)
        password_hash = await run_in_threadpool(
            hash_password,
            self._validate_password(password),
        )
        async with self.uow:
            user = await self.repository.find_by_username(normalized)
            if user is None:
                raise BusinessException(USER_NOT_FOUND, "user not found")
            user_id = user.id
        # Redis 属于外部系统，不能在数据库事务中调用。先撤销旧会话，即使
        # 随后的数据库写入失败也只是要求用户重新登录，不会保留泄漏会话。
        await self.refresh_store.revoke_user(user_id)
        async with self.uow:
            user = await self.repository.find_by_username(normalized)
            if user is None:
                raise BusinessException(USER_NOT_FOUND, "user not found")
            user.password_hash = password_hash
        return user

    async def list_users(self) -> list[User]:
        async with self.uow:
            return await self.repository.list_users()

    async def delete(self, username: str) -> None:
        async with self.uow:
            user = await self.repository.find_by_username(
                self._normalize_username(username)
            )
            if user is None:
                raise BusinessException(USER_NOT_FOUND, "user not found")
        await self.refresh_store.revoke_user(user.id)
        async with self.uow:
            user = await self.repository.find_by_username(
                self._normalize_username(username)
            )
            if user is None:
                return
            await self.repository.delete_user(user)

    async def delete_by_id(self, user_id: UUID, *, actor_user_id: UUID) -> None:
        """删除用户并撤销其刷新会话；禁止当前用户删除自己。"""
        if user_id == actor_user_id:
            raise BusinessException(
                CANNOT_DELETE_CURRENT_USER,
                "current user cannot be deleted",
            )
        async with self.uow:
            user = await self.repository.find_by_id(user_id)
            if user is None:
                raise BusinessException(USER_NOT_FOUND, "user not found")

        # Redis 是外部系统，不能在数据库事务期间调用。先撤销刷新会话，
        # 再删除用户；即使后续删除失败，也只会要求目标用户重新登录。
        await self.refresh_store.revoke_user(user_id)
        async with self.uow:
            user = await self.repository.find_by_id(user_id)
            if user is None:
                return
            await self.repository.delete_user(user)

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = username.strip().lower()
        if not 3 <= len(normalized) <= 64:
            raise BusinessException(
                INVALID_USER_INPUT,
                "username must contain 3 to 64 characters",
            )
        if not all(character.isalnum() or character in "._-" for character in normalized):
            raise BusinessException(
                INVALID_USER_INPUT,
                "username may contain letters, numbers, dot, underscore and hyphen",
            )
        return normalized

    @staticmethod
    def _validate_password(password: str) -> str:
        if not 8 <= len(password) <= 128:
            raise BusinessException(
                INVALID_USER_INPUT,
                "password must contain 8 to 128 characters",
            )
        return password
