import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.infrastructure.mineru_content import extract_mineru_block_content
from app.models.document_page import DocumentPage
from app.models.document_parse_block import DocumentParseBlock

DOCUMENT_IGNORED_BLOCK_TYPES = {"header", "footer", "page_number"}


@dataclass(frozen=True)
class DocumentParseOutput:
    blocks: list[DocumentParseBlock]
    pages: list[DocumentPage]


class DocumentParseBuilder:
    """把 MinerU 原始结果确定性地转换为原文块和页面。"""

    @classmethod
    def build(
        cls, *, document_id: UUID, result: dict[str, Any]
    ) -> DocumentParseOutput:
        content_list = cls._extract_content_list(result)
        blocks = cls._build_blocks(document_id=document_id, content_list=content_list)
        pages = cls._build_pages_from_blocks(document_id=document_id, blocks=blocks)
        return DocumentParseOutput(blocks=blocks, pages=pages)

    @staticmethod
    def _extract_content_list(result: dict[str, Any]) -> list[Any]:
        results = result.get("results")
        if not isinstance(results, dict) or not results:
            raise RuntimeError("MinerU result does not contain results")
        file_result = next(iter(results.values()))
        if not isinstance(file_result, dict):
            raise RuntimeError("invalid MinerU file result")
        raw_content_list = file_result.get("content_list")
        if not isinstance(raw_content_list, str):
            raise RuntimeError("MinerU result does not contain content_list")
        content_list = json.loads(raw_content_list)
        if not isinstance(content_list, list):
            raise RuntimeError("MinerU content_list must be a list")
        return content_list

    @classmethod
    def _build_blocks(
        cls, *, document_id: UUID, content_list: list[Any]
    ) -> list[DocumentParseBlock]:
        blocks: list[DocumentParseBlock] = []
        cursor = 0
        for item in content_list:
            if not isinstance(item, dict):
                continue
            content = extract_mineru_block_content(item)
            # 空视觉块没有可审计语义；MinerU 提供描述时 content 不为空。
            if not content.strip():
                continue
            page_index = item.get("page_idx")
            block_type = str(item.get("type") or "unknown").lower()
            char_start = cursor
            char_end = char_start + len(content)
            blocks.append(
                DocumentParseBlock(
                    id=uuid4(),
                    document_id=document_id,
                    block_index=len(blocks),
                    block_type=block_type,
                    content=content,
                    page_number=page_index + 1 if isinstance(page_index, int) else None,
                    bbox=cls._validated_bbox(item.get("bbox")),
                    text_level=(
                        item.get("text_level")
                        if isinstance(item.get("text_level"), int)
                        else None
                    ),
                    char_start=char_start,
                    char_end=char_end,
                    block_metadata=cls._block_metadata(item),
                )
            )
            cursor = char_end + 2
        return blocks

    @staticmethod
    def _validated_bbox(value: object) -> list[float] | None:
        """只保存可映射到 PDF 页面上的有效 MinerU 标准化坐标。"""
        if not isinstance(value, list) or len(value) != 4:
            return None
        if not all(isinstance(coordinate, (int, float)) for coordinate in value):
            return None
        x0, y0, x1, y1 = (float(coordinate) for coordinate in value)
        if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
            return None
        return [x0, y0, x1, y1]

    @staticmethod
    def _block_metadata(item: dict[str, Any]) -> dict[str, Any] | None:
        metadata = {
            key: item.get(key)
            for key in (
                "img_path",
                "image_caption",
                "image_footnote",
                "table_caption",
                "table_footnote",
                "chart_caption",
                "chart_footnote",
                "sub_type",
            )
            if item.get(key) is not None
        }
        return metadata or None

    @classmethod
    def _build_pages_from_blocks(
        cls, *, document_id: UUID, blocks: list[DocumentParseBlock]
    ) -> list[DocumentPage]:
        page_contents: dict[int, list[str]] = defaultdict(list)
        for block in blocks:
            if block.page_number is None or block.block_type in DOCUMENT_IGNORED_BLOCK_TYPES:
                continue
            page_contents[block.page_number].append(block.content)
        return [
            DocumentPage(
                document_id=document_id,
                page_number=page_number,
                content="\n\n".join(contents),
                bbox=None,
            )
            for page_number, contents in sorted(page_contents.items())
        ]

    @classmethod
    def _build_pages(
        cls, *, document_id: UUID, result: dict[str, Any]
    ) -> list[DocumentPage]:
        """兼容少量内部调用；新解析流程使用 build() 同时生成块和页面。"""
        blocks = cls._build_blocks(
            document_id=document_id,
            content_list=cls._extract_content_list(result),
        )
        return cls._build_pages_from_blocks(document_id=document_id, blocks=blocks)
