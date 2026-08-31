from uuid import UUID

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db
from app.models.document_parse_block import DocumentParseBlock


class DocumentParseBlockRepository:
    """管理文档的原始 MinerU 块，不在仓储层做语义切分。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_by_document(
        self, *, document_id: UUID, blocks: list[DocumentParseBlock]
    ) -> None:
        await self.session.execute(
            delete(DocumentParseBlock).where(DocumentParseBlock.document_id == document_id)
        )
        self.session.add_all(blocks)

    async def find_by_document(self, document_id: UUID) -> list[DocumentParseBlock]:
        result = await self.session.execute(
            select(DocumentParseBlock)
            .where(DocumentParseBlock.document_id == document_id)
            .order_by(DocumentParseBlock.block_index)
        )
        return list(result.scalars().all())

    async def find_by_document_and_page(
        self, *, document_id: UUID, page_number: int
    ) -> list[DocumentParseBlock]:
        result = await self.session.execute(
            select(DocumentParseBlock)
            .where(
                DocumentParseBlock.document_id == document_id,
                DocumentParseBlock.page_number == page_number,
            )
            .order_by(DocumentParseBlock.block_index)
        )
        return list(result.scalars().all())

    async def find_adjacent_page_blocks(
        self,
        *,
        document_id: UUID,
        page_number: int,
        per_side_limit: int = 50,
    ) -> tuple[list[DocumentParseBlock], list[DocumentParseBlock]]:
        """读取当前页前后的少量块，为跨页语义补充上下文。"""
        previous_result = await self.session.execute(
            select(DocumentParseBlock)
            .where(
                DocumentParseBlock.document_id == document_id,
                DocumentParseBlock.page_number < page_number,
            )
            .order_by(DocumentParseBlock.block_index.desc())
            .limit(per_side_limit)
        )
        next_result = await self.session.execute(
            select(DocumentParseBlock)
            .where(
                DocumentParseBlock.document_id == document_id,
                DocumentParseBlock.page_number > page_number,
            )
            .order_by(DocumentParseBlock.block_index)
            .limit(per_side_limit)
        )
        # 前文查询采用倒序以获取最近的块，交给调用方前恢复原文顺序。
        previous = list(reversed(previous_result.scalars().all()))
        return previous, list(next_result.scalars().all())


def get_document_parse_block_repository(
    session: AsyncSession = Depends(get_db),
) -> DocumentParseBlockRepository:
    return DocumentParseBlockRepository(session)
