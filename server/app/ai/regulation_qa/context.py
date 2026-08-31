import re
from collections.abc import Mapping

MAX_EVIDENCE_SPAN_CHARACTERS = 600

_CLAUSE_BOUNDARY = re.compile(
    r"(?=（(?:[一二三四五六七八九十百零〇两]+|\d+)）)"
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；])")


def build_evidence_spans(chunk: Mapping[str, object]) -> list[dict[str, str]]:
    """把 Chunk 确定性切成可引用的原文片段，不改变 ES 中的 Chunk。

    优先保留编号条款和自然段的完整性；超长段落再按句子打包。ID 只在本次
    回答上下文中使用，模型只能选择 ID，不能自行生成展示给用户的原文。
    """

    content = str(chunk["content"])
    structural_parts: list[str] = []
    for line in content.splitlines() or [content]:
        if not line.strip():
            continue
        structural_parts.extend(
            part for part in _CLAUSE_BOUNDARY.split(line) if part.strip()
        )

    parts: list[str] = []
    for part in structural_parts:
        if len(part) <= MAX_EVIDENCE_SPAN_CHARACTERS:
            parts.append(part.strip())
            continue
        sentence_buffer = ""
        for sentence in _SENTENCE_BOUNDARY.split(part):
            if not sentence:
                continue
            if sentence_buffer and len(sentence_buffer) + len(sentence) > MAX_EVIDENCE_SPAN_CHARACTERS:
                parts.append(sentence_buffer.strip())
                sentence_buffer = ""
            while len(sentence) > MAX_EVIDENCE_SPAN_CHARACTERS:
                parts.append(sentence[:MAX_EVIDENCE_SPAN_CHARACTERS].strip())
                sentence = sentence[MAX_EVIDENCE_SPAN_CHARACTERS:]
            sentence_buffer += sentence
        if sentence_buffer.strip():
            parts.append(sentence_buffer.strip())

    chunk_id = str(chunk["chunk_id"])
    return [
        {
            "evidence_id": f"{chunk_id}:e{index}",
            "chunk_id": chunk_id,
            "content": part,
        }
        for index, part in enumerate(parts, start=1)
        if part
    ]


def render_retrieved_chunk(chunk: Mapping[str, object]) -> str:
    """统一渲染交给安全模型和回答模型的完整不可信 Chunk。"""
    return (
        f"[chunk_id={chunk['chunk_id']}]\n"
        f"[regulation_id={chunk['regulation_id']}]\n"
        f"[title={chunk['title']}]\n"
        f"[page_start={chunk['page_start']}]\n"
        f"[page_end={chunk['page_end']}]\n"
        f"{chunk['content']}"
    )


def render_chunk_for_answer(chunk: Mapping[str, object]) -> str:
    """为回答模型渲染带可信片段 ID 的 Chunk。"""

    evidence = "\n".join(
        f"[evidence_id={span['evidence_id']}]\n{span['content']}"
        for span in build_evidence_spans(chunk)
    )
    return (
        f"[chunk_id={chunk['chunk_id']}]\n"
        f"[regulation_id={chunk['regulation_id']}]\n"
        f"[title={chunk['title']}]\n"
        f"[page_start={chunk['page_start']}]\n"
        f"[page_end={chunk['page_end']}]\n"
        f"{evidence}"
    )
