import json
from typing import Any

from pydantic import BaseModel


def serialize_tool_result(value: Any, *, max_chars: int) -> str:
    """序列化合法 JSON；超限时返回可解析的预览信封，不截断 JSON 语法。"""

    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized

    # JSON 转义会让预览长度非线性增长，使用二分查找选择仍能放入上限的
    # 最大前缀。模型会明确知道结果不完整，不会把残缺 JSON 当成完整对象。
    low, high = 0, len(serialized)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = json.dumps(
            {
                "truncated": True,
                "originalCharacters": len(serialized),
                "contentPreview": serialized[:middle],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(candidate) <= max_chars:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if not best:
        raise ValueError("tool result limit is too small for truncation metadata")
    return best
