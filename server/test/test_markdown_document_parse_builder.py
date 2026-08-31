from uuid import uuid4

import pytest

from app.services.markdown_document_parse_builder import MarkdownDocumentParseBuilder


def test_markdown_builder_preserves_offsets_and_complete_structural_blocks() -> None:
    source = (
        "\ufeff# 隐私规则\r\n\r\n"
        "普通段落包含中文。\r\n\r\n"
        "| 字段 | 要求 |\r\n"
        "| --- | --- |\r\n"
        "| 手机号 | 必须说明用途 |\r\n\r\n"
        "```python\r\nprint('不要执行')\r\n```\r\n"
    )

    output = MarkdownDocumentParseBuilder.build(document_id=uuid4(), source=source)

    normalized = MarkdownDocumentParseBuilder.normalize_source(source)
    assert [block.block_type for block in output.blocks] == [
        "heading",
        "paragraph",
        "table",
        "code",
    ]
    assert output.blocks[2].content == (
        "| 字段 | 要求 |\n| --- | --- |\n| 手机号 | 必须说明用途 |"
    )
    assert output.blocks[3].content == "```python\nprint('不要执行')\n```"
    for block in output.blocks:
        assert normalized[block.char_start : block.char_end] == block.content
        assert block.bbox is None
        assert block.block_metadata is not None
        assert block.block_metadata["sourceType"] == "MARKDOWN"


def test_markdown_builder_groups_only_between_complete_blocks() -> None:
    long_paragraph = "规则内容" * 700
    table = "| A | B |\n| --- | --- |\n" + "\n".join(
        f"| {index} | 条款{index} |" for index in range(200)
    )
    source = f"# 标题\n\n{long_paragraph}\n\n{table}\n\n结尾"

    output = MarkdownDocumentParseBuilder.build(document_id=uuid4(), source=source)

    assert len(output.pages) >= 3
    table_block = next(block for block in output.blocks if block.block_type == "table")
    assert table_block.content == table
    assert table_block.page_number is not None
    assert table in output.pages[table_block.page_number - 1].content


def test_plain_text_is_valid_markdown_input() -> None:
    source = "第一条规则。\n第二行仍属于同一个普通文本段落。"

    output = MarkdownDocumentParseBuilder.build(document_id=uuid4(), source=source)

    assert len(output.blocks) == 1
    assert output.blocks[0].block_type == "paragraph"
    assert output.pages[0].content == source


def test_empty_markdown_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        MarkdownDocumentParseBuilder.build(document_id=uuid4(), source=" \n\t")
