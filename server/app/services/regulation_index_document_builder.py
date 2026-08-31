import re

from app.ai.embedding import EmbeddingService
from app.models.regulation import Regulation
from app.models.regulation_chunk import RegulationChunk

REGULATION_EMBEDDING_TABLE_FRAGMENT_SIZE = 1000


class RegulationIndexDocumentBuilder:
    """Build bounded embedding inputs and Elasticsearch document payloads."""

    @staticmethod
    def _build_embedding_text(
        *,
        regulation: Regulation,
        chunk: RegulationChunk,
        content: str | None = None,
    ) -> str:
        """组合检索语义，避免只向量化脱离上下文的一小段原文。"""

        metadata = chunk.chunk_metadata or {}
        parts = [
            f"标题：{regulation.title}",
            f"知识类型：{regulation.source_type.value}",
            f"适用地区：{regulation.jurisdiction}",
        ]

        if chunk.chapter:
            parts.append(f"章节：{chunk.chapter}")

        if chunk.article_number:
            parts.append(f"条款：{chunk.article_number}")

        context_heading = metadata.get("contextHeading")
        if context_heading:
            parts.append(f"上文标题：{context_heading}")

        field_names = {
            "ruleType": "规则类型",
            "subject": "责任主体",
            "action": "行为要求",
            "condition": "适用条件",
            "exception": "例外情况",
            "consequence": "违规后果",
        }

        for key, label in field_names.items():
            value = metadata.get(key)
            if value is not None and str(value).strip():
                parts.append(f"{label}：{value}")

        parts.append(f"原文：{chunk.content if content is None else content}")
        return "\n".join(parts)

    @classmethod
    async def _build_index_documents(
        cls,
        *,
        embedding: EmbeddingService,
        regulation: Regulation,
        chunks: list[RegulationChunk],
    ) -> list[dict]:
        """生成 ES 文档；大表格对应多个共享来源 Chunk ID 的检索片段。"""
        content_groups = [cls._build_embedding_contents(chunk=chunk) for chunk in chunks]
        texts = [
            cls._build_embedding_text(
                regulation=regulation,
                chunk=chunk,
                content=content,
            )
            for chunk, contents in zip(chunks, content_groups, strict=True)
            for content in contents
        ]
        # EmbeddingService 会验证返回数量、维度和非法浮点数。
        vectors = await embedding.embed_documents(texts)
        documents: list[dict] = []
        vector_offset = 0
        for chunk, contents in zip(chunks, content_groups, strict=True):
            for fragment_index, content in enumerate(contents):
                documents.append(
                    cls._build_index_chunk(
                        regulation=regulation,
                        chunk=chunk,
                        embedding=vectors[vector_offset],
                        content=content,
                        document_id=(
                            str(chunk.id) if len(contents) == 1 else f"{chunk.id}:{fragment_index}"
                        ),
                    )
                )
                vector_offset += 1
        return documents

    @staticmethod
    def _build_embedding_contents(
        *,
        chunk: RegulationChunk,
    ) -> list[str]:
        """返回一个普通正文或多个不破坏原表的表格检索片段。"""
        metadata = chunk.chunk_metadata or {}
        block_types = {str(value).lower() for value in metadata.get("blockTypes", [])}
        if (
            "table" not in block_types
            or len(chunk.content) <= REGULATION_EMBEDDING_TABLE_FRAGMENT_SIZE
        ):
            return [chunk.content]
        return RegulationIndexDocumentBuilder._split_table_for_embedding(
            chunk.content,
            max_chars=REGULATION_EMBEDDING_TABLE_FRAGMENT_SIZE,
        )

    @staticmethod
    def _build_embedding_fragments(
        *,
        regulation: Regulation,
        chunk: RegulationChunk,
    ) -> list[str]:
        """为超大表格生成检索片段，数据库 Chunk 本身保持完整。"""
        return [
            RegulationIndexDocumentBuilder._build_embedding_text(
                regulation=regulation,
                chunk=chunk,
                content=fragment,
            )
            for fragment in RegulationIndexDocumentBuilder._build_embedding_contents(
                chunk=chunk,
            )
        ]

    @staticmethod
    def _split_table_for_embedding(
        content: str,
        *,
        max_chars: int,
    ) -> list[str]:
        """按表格行切分检索输入，并在每段重复表头。"""
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0")

        prefix = ""
        rows: list[str]
        if "</tr>" in content.lower():
            # HTML 表格可能没有换行，按 tr 结束标签恢复行边界。
            rows = [
                match.group(0)
                for match in re.finditer(
                    r".*?</tr>|.+$",
                    content,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            if rows:
                prefix = rows.pop(0)
        else:
            rows = content.splitlines(keepends=True)
            if len(rows) >= 2 and "|" in rows[0] and re.match(r"^\s*\|?\s*:?-+", rows[1]):
                prefix = "".join(rows[:2])
                rows = rows[2:]

        # 极端宽表头只影响检索表示，原始完整表格仍在 PostgreSQL/ES content。
        if len(prefix) >= max_chars:
            prefix = prefix[: max(1, max_chars // 2)]
        body_budget = max(1, max_chars - len(prefix))
        pieces: list[str] = []
        current = ""
        for row in rows or [content]:
            row_parts = [
                row[index : index + body_budget] for index in range(0, len(row), body_budget)
            ] or [""]
            for row_part in row_parts:
                if current and len(current) + len(row_part) > body_budget:
                    pieces.append(f"{prefix}{current}")
                    current = ""
                current += row_part
        if current or not pieces:
            pieces.append(f"{prefix}{current}")
        return pieces

    @staticmethod
    def _build_index_chunk(
        *,
        regulation: Regulation,
        chunk: RegulationChunk,
        embedding: list[float],
        content: str | None = None,
        document_id: str | None = None,
    ) -> dict:
        """把数据库对象转换为 ES 文档，枚举和日期统一序列化。"""

        metadata = chunk.chunk_metadata or {}
        page_start = metadata.get("pageStart", chunk.page_number)
        page_end = metadata.get("pageEnd", chunk.page_number)

        return {
            "id": str(chunk.id),
            "document_id": document_id or str(chunk.id),
            "regulation_id": str(regulation.id),
            "uploaded_by": str(regulation.uploaded_by),
            "visibility": regulation.visibility.value,
            "category": regulation.category.value,
            "source_type": regulation.source_type.value,
            "language": regulation.language,
            "jurisdiction": regulation.jurisdiction,
            "enabled": regulation.enabled,
            "title": regulation.title,
            "authority": regulation.authority,
            "effective_date": (
                regulation.effective_date.isoformat() if regulation.effective_date else None
            ),
            "expiration_date": (
                regulation.expiration_date.isoformat() if regulation.expiration_date else None
            ),
            "chunk_index": chunk.chunk_index,
            "article_number": chunk.article_number,
            "chapter": chunk.chapter,
            "page_number": chunk.page_number,
            "page_start": page_start,
            "page_end": page_end,
            "content": chunk.content if content is None else content,
            "rule_type": metadata.get("ruleType"),
            "subject": metadata.get("subject"),
            "action": metadata.get("action"),
            "condition": metadata.get("condition"),
            "exception": metadata.get("exception"),
            "consequence": metadata.get("consequence"),
            "embedding": embedding,
        }
