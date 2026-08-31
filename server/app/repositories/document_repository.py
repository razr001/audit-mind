from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db
from app.models.document import Document, DocumentStatus

DocumentSortField = Literal["createdAt", "originalFilename", "fileSize", "status"]
SortOrder = Literal["asc", "desc"]


class DocumentRepository:
    """集中封装 Document 查询和原子状态流转 SQL。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, document: Document):
        """加入当前 Session 并 flush，使主键等数据库字段立即可用。"""
        self.session.add(document)
        await self.session.flush()
        return document

    async def find_by_id(self, document_id) -> Document | None:
        result = await self.session.execute(select(Document).where(Document.id == document_id))
        return result.scalar_one_or_none()

    async def find_by_id_and_user(self, document_id: UUID, user_id: UUID) -> Document | None:
        """按文档和用户同时过滤，避免 Service 遗漏数据隔离条件。"""
        result = await self.session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_page_by_user(
        self,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
        *,
        sort_by: DocumentSortField = "createdAt",
        sort_order: SortOrder = "desc",
    ) -> tuple[list[Document], int]:
        """Return one user-scoped page using an allow-listed, stable ordering."""
        sort_columns = {
            "createdAt": Document.created_at,
            "originalFilename": Document.original_filename,
            "fileSize": Document.file_size,
            "status": Document.status,
        }
        column = sort_columns[sort_by]
        order = column.asc() if sort_order == "asc" else column.desc()
        id_order = Document.id.asc() if sort_order == "asc" else Document.id.desc()
        result = await self.session.execute(
            select(Document)
            .where(
                Document.user_id == user_id,
            )
            .order_by(
                order,
                id_order,
            )
            .offset(offset)
            .limit(limit)
        )
        documents = list(result.scalars().all())

        total = (
            await self.session.scalar(
                select(func.count(Document.id)).where(
                    Document.user_id == user_id,
                )
            )
            or 0
        )

        return documents, total

    async def claim_for_parse(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        started_at: datetime,
        stale_before: datetime,
    ) -> Document | None:
        """原子领取新解析，或接管没有外部任务可继续的超时提交阶段。"""
        # 两个并发请求只有一个能把允许的旧状态更新为 PARSING；
        # 另一个请求会因 WHERE 条件不再成立而得到 None。
        statement = (
            update(Document)
            .where(
                Document.id == document_id,
                Document.user_id == user_id,
                or_(
                    Document.status.in_(
                        [
                            DocumentStatus.UPLOADED,
                            DocumentStatus.FAILED,
                        ]
                    ),
                    and_(
                        Document.status == DocumentStatus.PARSING,
                        Document.parse_task_id.is_(None),
                        or_(
                            Document.parse_started_at.is_(None),
                            Document.parse_started_at <= stale_before,
                        ),
                    ),
                ),
            )
            .values(
                status=DocumentStatus.PARSING,
                lock_version=Document.lock_version + 1,
                parse_task_id=None,
                parse_error=None,
                parse_started_at=started_at,
                parse_completed_at=None,
            )
            .returning(Document)
        )

        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def find_by_id_and_user_for_update(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> Document | None:
        """
        查询用户文档并获取数据库行锁。

        锁只在当前事务内有效，用于提交最终状态前防止并发请求覆盖结果。
        """
        result = await self.session.execute(
            select(Document)
            .where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return result.scalar_one_or_none()

def get_document_repository(session: AsyncSession = Depends(get_db)):
    return DocumentRepository(session)
