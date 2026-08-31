from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operation_log import OperationLog


class OperationLogRepository:
    """只负责写入操作快照，事务由业务 Service 的 UoW 管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(self, operation_log: OperationLog) -> OperationLog:
        self.session.add(operation_log)
        await self.session.flush()
        return operation_log
