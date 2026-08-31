from uuid import UUID

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db
from app.models.document_page import DocumentPage


class DocumentPageRepository:
    """管理用于原文预览和定位的文档分页内容。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_by_document(
        self,
        *,
        document_id: UUID,
        pages: list[DocumentPage],
    ) -> None:
        """以一次解析结果整体替换文档原有页面。"""
        await self.session.execute(
            delete(DocumentPage).where(
                DocumentPage.document_id == document_id,
            )
        )

        self.session.add_all(pages)

    async def find_by_document(self, document_id: UUID) -> list[DocumentPage]:
        result = await self.session.execute(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        )
        return list(result.scalars().all())

    async def find_by_document_and_number(
        self,
        *,
        document_id: UUID,
        page_number: int,
    ) -> DocumentPage | None:
        result = await self.session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == page_number,
            )
        )
        return result.scalar_one_or_none()


def get_document_page_repository(
    session: AsyncSession = Depends(get_db),
) -> DocumentPageRepository:
    return DocumentPageRepository(session)
