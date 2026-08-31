from dataclasses import dataclass
from uuid import UUID, uuid4

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.models.document_page import DocumentPage
from app.models.document_parse_block import DocumentParseBlock
from app.services.document_parse_builder import DocumentParseOutput

MARKDOWN_AUDIT_UNIT_TARGET_CHARACTERS = 2_600


@dataclass(frozen=True)
class _SourceBlock:
    """Markdown 顶层块在规范化原文中的精确位置。"""

    block_type: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


class MarkdownDocumentParseBuilder:
    """把 Markdown/纯文本解析为可审计块和逻辑审计单元。

    Markdown 没有稳定的物理页概念，因此 ``DocumentPage.page_number`` 在该来源
    下表示逻辑审计单元编号。分组只发生在顶层块之间，表格、列表、引用和代码块
    不会因达到目标字符数而被截断。
    """

    _parser = MarkdownIt("commonmark").enable("table")

    @classmethod
    def normalize_source(cls, source: str) -> str:
        """统一换行并去掉 UTF-8 BOM，使保存原文和字符偏移使用同一份文本。"""
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.removeprefix("\ufeff")

    @classmethod
    def build(cls, *, document_id: UUID, source: str) -> DocumentParseOutput:
        normalized = cls.normalize_source(source)
        if not normalized.strip():
            raise ValueError("Markdown content must not be empty")

        source_blocks = cls._extract_source_blocks(normalized)
        blocks = cls._build_blocks(document_id=document_id, source=normalized, spans=source_blocks)
        pages = cls._assign_units_and_build_pages(
            document_id=document_id,
            source=normalized,
            blocks=blocks,
        )
        return DocumentParseOutput(blocks=blocks, pages=pages)

    @classmethod
    def _extract_source_blocks(cls, source: str) -> list[_SourceBlock]:
        tokens = cls._parser.parse(source)
        line_offsets = cls._line_offsets(source)
        spans: list[_SourceBlock] = []
        seen: set[tuple[int, int]] = set()

        for token in tokens:
            if token.level != 0 or token.map is None or not cls._is_top_level_block(token):
                continue
            line_start, line_end_exclusive = token.map
            key = (line_start, line_end_exclusive)
            if key in seen or line_start >= line_end_exclusive:
                continue
            seen.add(key)
            char_start = line_offsets[line_start]
            char_end = line_offsets[line_end_exclusive]
            # 行范围通常包含行末换行；偏移必须指向有意义原文，不能用 strip()
            # 后再反推位置，否则重复文本会得到错误坐标。
            if char_end > char_start and source[char_end - 1 : char_end] == "\n":
                char_end -= 1
            if not source[char_start:char_end].strip():
                continue
            spans.append(
                _SourceBlock(
                    block_type=cls._block_type(token),
                    char_start=char_start,
                    char_end=char_end,
                    line_start=line_start + 1,
                    line_end=line_end_exclusive,
                )
            )

        spans.sort(key=lambda span: (span.char_start, span.char_end))
        if spans:
            return spans

        # CommonMark 通常会为普通文本产生 paragraph；这里仍保留安全降级，确保
        # 未来解析器插件变化时不会悄悄丢失非空内容。
        end = len(source.rstrip("\n"))
        return [
            _SourceBlock(
                block_type="paragraph",
                char_start=0,
                char_end=end,
                line_start=1,
                line_end=source.count("\n") + 1,
            )
        ]

    @staticmethod
    def _line_offsets(source: str) -> list[int]:
        offsets = [0]
        offsets.extend(index + 1 for index, character in enumerate(source) if character == "\n")
        if offsets[-1] != len(source):
            offsets.append(len(source))
        return offsets

    @staticmethod
    def _is_top_level_block(token: Token) -> bool:
        return token.type in {
            "heading_open",
            "paragraph_open",
            "bullet_list_open",
            "ordered_list_open",
            "blockquote_open",
            "table_open",
            "fence",
            "code_block",
            "html_block",
            "hr",
        }

    @staticmethod
    def _block_type(token: Token) -> str:
        return {
            "heading_open": "heading",
            "paragraph_open": "paragraph",
            "bullet_list_open": "list",
            "ordered_list_open": "list",
            "blockquote_open": "blockquote",
            "table_open": "table",
            "fence": "code",
            "code_block": "code",
            "html_block": "html",
            "hr": "thematic_break",
        }.get(token.type, "paragraph")

    @staticmethod
    def _build_blocks(
        *,
        document_id: UUID,
        source: str,
        spans: list[_SourceBlock],
    ) -> list[DocumentParseBlock]:
        return [
            DocumentParseBlock(
                id=uuid4(),
                document_id=document_id,
                block_index=index,
                block_type=span.block_type,
                content=source[span.char_start : span.char_end],
                page_number=None,
                bbox=None,
                text_level=None,
                char_start=span.char_start,
                char_end=span.char_end,
                block_metadata={
                    "sourceType": "MARKDOWN",
                    "lineStart": span.line_start,
                    "lineEnd": span.line_end,
                },
            )
            for index, span in enumerate(spans)
        ]

    @classmethod
    def _assign_units_and_build_pages(
        cls,
        *,
        document_id: UUID,
        source: str,
        blocks: list[DocumentParseBlock],
    ) -> list[DocumentPage]:
        units: list[list[DocumentParseBlock]] = []
        current: list[DocumentParseBlock] = []
        current_size = 0

        for block in blocks:
            separator_size = 2 if current else 0
            if (
                current
                and current_size + separator_size + len(block.content)
                > MARKDOWN_AUDIT_UNIT_TARGET_CHARACTERS
            ):
                units.append(current)
                current = []
                current_size = 0
            current.append(block)
            current_size += (2 if current_size else 0) + len(block.content)
        if current:
            units.append(current)

        pages: list[DocumentPage] = []
        for unit_number, unit_blocks in enumerate(units, start=1):
            for block in unit_blocks:
                block.page_number = unit_number
            unit_start = unit_blocks[0].char_start
            unit_end = unit_blocks[-1].char_end
            pages.append(
                DocumentPage(
                    document_id=document_id,
                    page_number=unit_number,
                    # 使用原文切片保留块之间的空行和 Markdown 语法，供前端渲染。
                    content=source[unit_start:unit_end],
                    bbox=None,
                )
            )
        return pages
