from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regulation_parse_block import RegulationParseBlock


class RegulationParseBlockRepository:
    """管理 MinerU 原始块；不在 Repository 中进行语义切分。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_by_regulation(
        self,
        *,
        regulation_id: UUID,
        blocks: list[RegulationParseBlock],
    ) -> None:
        """在一个事务中用本次 MinerU 结果替换旧 ParseBlock。"""
        await self.session.execute(
            delete(RegulationParseBlock).where(
                RegulationParseBlock.regulation_id == regulation_id,
            )
        )
        self.session.add_all(blocks)

    async def find_by_regulation(
        self,
        regulation_id: UUID,
    ) -> list[RegulationParseBlock]:
        """按 block_index 返回可重新拼接为规范原文的全部块。"""
        result = await self.session.execute(
            select(RegulationParseBlock)
            .where(RegulationParseBlock.regulation_id == regulation_id)
            .order_by(RegulationParseBlock.block_index)
        )
        return list(result.scalars().all())

    async def find_by_id(
        self,
        block_id: UUID,
    ) -> RegulationParseBlock | None:
        """按主键获取块，访问权限由上层根据 regulation_id 继续校验。"""
        result = await self.session.execute(
            select(RegulationParseBlock).where(
                RegulationParseBlock.id == block_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_by_regulation_and_page(
        self,
        *,
        regulation_id: UUID,
        page_number: int,
        limit: int,
    ) -> list[RegulationParseBlock]:
        """返回一页内按阅读顺序排列的文本和视觉块。"""
        result = await self.session.execute(
            select(RegulationParseBlock)
            .where(
                RegulationParseBlock.regulation_id == regulation_id,
                RegulationParseBlock.page_number == page_number,
            )
            .order_by(RegulationParseBlock.block_index)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_max_page_number(self, regulation_id: UUID) -> int:
        """返回已解析块覆盖的最大 PDF 页码；未解析时返回 0。"""
        return (
            await self.session.scalar(
                select(func.max(RegulationParseBlock.page_number)).where(
                    RegulationParseBlock.regulation_id == regulation_id,
                )
            )
            or 0
        )
