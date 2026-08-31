import json
from uuid import uuid4

from app.services.document_parse_builder import DocumentParseBuilder


def test_document_builder_preserves_blocks_bbox_and_source_segments() -> None:
    document_id = uuid4()
    result = {
        "results": {
            "sample.pdf": {
                "content_list": json.dumps(
                    [
                        {"type": "header", "text": "重复页眉", "page_idx": 0, "bbox": [10, 10, 990, 40]},
                        {"type": "text", "text": "  用户应当明示处理目的。  ", "page_idx": 0, "bbox": [100, 100, 900, 180]},
                        {"type": "image", "content": "图片展示了默认勾选同意按钮。", "page_idx": 0, "bbox": [200, 220, 800, 600]},
                    ],
                    ensure_ascii=False,
                )
            }
        }
    }

    output = DocumentParseBuilder.build(document_id=document_id, result=result)

    assert len(output.blocks) == 3
    assert output.blocks[1].content == "  用户应当明示处理目的。  "
    assert output.blocks[1].bbox == [100.0, 100.0, 900.0, 180.0]
    assert output.pages[0].content == "  用户应当明示处理目的。  \n\n图片展示了默认勾选同意按钮。"
    assert [block.block_type for block in output.blocks] == ["header", "text", "image"]


def test_document_builder_drops_invalid_bbox_without_dropping_content() -> None:
    blocks = DocumentParseBuilder._build_blocks(
        document_id=uuid4(),
        content_list=[{"type": "text", "text": "正文仍然有效", "page_idx": 0, "bbox": [-1, 20, 1001, 40]}],
    )

    assert len(blocks) == 1
    assert blocks[0].bbox is None
