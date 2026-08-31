from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.models.document_parse_block import DocumentParseBlock

MAX_AUDIT_INPUT_CHARACTERS = 3_000
ADJACENT_CONTEXT_CHARACTERS = 200
# 前后文各预留 200 字，因此当前批次正文最多 2600 字，保证提交给模型和
# 规则召回的文档文本合计永远不超过 3000 字。
MAX_AUDIT_BATCH_CHARACTERS = (
    MAX_AUDIT_INPUT_CHARACTERS - ADJACENT_CONTEXT_CHARACTERS * 2
)
IGNORED_AUDIT_BLOCK_TYPES = frozenset({"header", "footer", "page_number"})


@dataclass(frozen=True)
class AuditInputBlock:
    """提交给模型的证据片段；ID 仍指向原始 ParseBlock，便于结果回溯和高亮。"""

    id: UUID
    block_type: str
    content: str
    bbox: list | None
    char_start: int
    char_end: int


def build_adjacent_context(
    *,
    previous_blocks: Sequence[DocumentParseBlock],
    next_blocks: Sequence[DocumentParseBlock],
) -> tuple[str, str]:
    """取前文末尾和后文开头各 200 字，不改变当前页证据坐标。"""
    previous_text = "\n".join(
        block.content
        for block in previous_blocks
        if block.block_type not in IGNORED_AUDIT_BLOCK_TYPES and block.content.strip()
    )
    next_text = "\n".join(
        block.content
        for block in next_blocks
        if block.block_type not in IGNORED_AUDIT_BLOCK_TYPES and block.content.strip()
    )
    return (
        previous_text[-ADJACENT_CONTEXT_CHARACTERS:],
        next_text[:ADJACENT_CONTEXT_CHARACTERS],
    )


def build_audit_batches(
    blocks: Sequence[DocumentParseBlock],
) -> list[list[AuditInputBlock]]:
    """为相邻上下文预留空间，确保单次文档输入合计不超过 3000 字。"""
    batches: list[list[AuditInputBlock]] = []
    current: list[AuditInputBlock] = []
    size = 0
    for block in blocks:
        # 普通文本、表格、公式都必须遵守模型输入上限。超长块切片后仍使用
        # 原 ParseBlock ID；模型结果最终仍能定位到原始 PDF bbox。
        segments = [
            (
                start,
                block.content[start : start + MAX_AUDIT_BATCH_CHARACTERS],
            )
            for start in range(0, len(block.content), MAX_AUDIT_BATCH_CHARACTERS)
        ]
        for segment_offset, segment in segments:
            if current and size + len(segment) > MAX_AUDIT_BATCH_CHARACTERS:
                batches.append(current)
                current = []
                size = 0
            current.append(
                AuditInputBlock(
                    id=block.id,
                    block_type=block.block_type,
                    content=segment,
                    bbox=block.bbox,
                    char_start=block.char_start + segment_offset,
                    char_end=block.char_start + segment_offset + len(segment),
                )
            )
            size += len(segment)
    if current:
        batches.append(current)
    return batches


def build_batch_contexts(
    *,
    batches: Sequence[Sequence[AuditInputBlock]],
    page_context_before: str,
    page_context_after: str,
) -> list[tuple[str, str]]:
    """为每个审计批次补充相邻 200 字，不让整页上下文重复干扰召回。"""
    contexts: list[tuple[str, str]] = []
    for index, batch in enumerate(batches):
        if index == 0:
            context_before = page_context_before
        else:
            previous_text = "\n".join(block.content for block in batches[index - 1])
            context_before = previous_text[-ADJACENT_CONTEXT_CHARACTERS:]
        if index == len(batches) - 1:
            context_after = page_context_after
        else:
            next_text = "\n".join(block.content for block in batches[index + 1])
            context_after = next_text[:ADJACENT_CONTEXT_CHARACTERS]
        contexts.append((context_before, context_after))
    return contexts
