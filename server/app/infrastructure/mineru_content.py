from typing import Any


def extract_mineru_block_content(item: dict[str, Any]) -> str:
    """提取 MinerU 块的专业识别结果，并保留原始字符串内容。

    兼容当前 content_list 的文本、公式、表格、代码、列表、图片和图表，
    同时对 content_list_v2 的结构化 content 做基础兼容。这里不调用其他
    模型，也不覆盖 MinerU 已经识别出的 LaTeX、HTML 或 Markdown。
    """
    for key in ("text", "table_body", "content", "code_body"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (dict, list)):
            structured_text = _extract_structured_text(value)
            if structured_text.strip():
                return structured_text

    list_items = item.get("list_items")
    if isinstance(list_items, list):
        values = [value for value in list_items if isinstance(value, str)]
        if values:
            return "\n".join(values)

    # 部分图片块只有文档自身的标题或脚注，没有视觉分析 content。
    captions: list[str] = []
    for key in (
        "image_caption",
        "image_footnote",
        "table_caption",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "code_caption",
        "code_footnote",
    ):
        value = item.get(key)
        if isinstance(value, list):
            captions.extend(entry for entry in value if isinstance(entry, str))

    return "\n\n".join(captions)


def _extract_structured_text(value: dict | list) -> str:
    """从 MinerU v2 的 span/结构化字段中按原顺序提取文字。"""
    if isinstance(value, list):
        parts = [
            _extract_structured_text(entry) if isinstance(entry, (dict, list)) else entry
            for entry in value
            if isinstance(entry, (str, dict, list))
        ]
        return "".join(part for part in parts if isinstance(part, str))

    # content_list_v2 的不同块使用不同字段名。只读取承载正文的字段，
    # 不读取 image_source.path、URL 等结构信息。
    for key in (
        "title_content",
        "paragraph_content",
        "math_content",
        "code_content",
        "algorithm_content",
        "list_items",
        "html",
        "content",
        "image_caption",
        "table_caption",
        "chart_caption",
    ):
        entry = value.get(key)
        if isinstance(entry, str) and entry.strip():
            return entry
        if isinstance(entry, (dict, list)):
            text = _extract_structured_text(entry)
            if text.strip():
                return text

    return ""
