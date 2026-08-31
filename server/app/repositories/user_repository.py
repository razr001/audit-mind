from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(func.lower(User.username) == username.lower())
        )
        return result.scalar_one_or_none()

    async def find_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def list_users(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.username, User.id))
        return list(result.scalars().all())

    async def save(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def delete_user(self, user: User) -> None:
        await self.session.delete(user)
