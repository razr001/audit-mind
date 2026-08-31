from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.infrastructure.db.engine import engine

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
# expire_on_commit=False 允许 Service 在事务提交后序列化刚保存的实体，
# 不会因为读取字段而隐式发起一次数据库查询。


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """每个 HTTP 请求提供独立 Session，并在请求结束时释放其资源。"""
    async with async_session_factory() as session:
        yield session
