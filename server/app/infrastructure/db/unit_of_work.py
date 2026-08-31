from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db


class UnitOfWork:
    """用上下文管理器统一控制一个业务事务的提交和回滚。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # with 块抛出异常时回滚；正常退出时提交。
        # Service 应避免把慢速网络请求放进该上下文，以免长期占用事务。
        if exc_type:
            await self.rollback()
        else:
            try:
                await self.commit()
            except BaseException:
                # commit 失败后 Session 会进入待回滚状态。这里必须先
                # 清理失败事务，调用方才能记录 FAILED 或继续查询。
                await self.rollback()
                raise

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


def get_uow(session: AsyncSession = Depends(get_db)):
    """让同一次请求的 UnitOfWork 与 FastAPI 提供的 Session 共用连接上下文。"""
    return UnitOfWork(session)
