from collections.abc import Iterable

from langdetect import DetectorFactory, LangDetectException, detect

# langdetect 对很短或语言混合的文本存在概率分支；固定 seed 保证重试结果一致。
DetectorFactory.seed = 0

MAX_LANGUAGE_SAMPLE_CHARS = 50_000


def detect_content_language(
    contents: Iterable[str],
    *,
    fallback: str,
) -> str:
    """从解析后的正文识别语言，并返回适合持久化的语言标签。"""
    sample_parts: list[str] = []
    sample_length = 0
    for content in contents:
        if not content or not content.strip():
            continue
        remaining = MAX_LANGUAGE_SAMPLE_CHARS - sample_length
        if remaining <= 0:
            break
        part = content[:remaining]
        sample_parts.append(part)
        sample_length += len(part)

    sample = "\n".join(sample_parts)
    if not sample.strip():
        return fallback

    try:
        return normalize_language_tag(detect(sample))
    except LangDetectException:
        return fallback


def default_language_for_jurisdiction(jurisdiction: str) -> str:
    """无可识别正文时，使用法域提供一个可预测的回退值。"""
    normalized = jurisdiction.strip().upper()
    if normalized == "TW":
        return "zh-TW"
    if normalized in {"CN", "HK", "MO"}:
        return "zh-CN"
    return "en"


def normalize_language_tag(language: str) -> str:
    """把检测器返回值规范为项目使用的 BCP 47 风格标签。"""
    parts = language.replace("_", "-").split("-")
    primary = parts[0].lower()
    if len(parts) == 1:
        return primary
    region = parts[1].upper() if len(parts[1]) == 2 else parts[1].title()
    return f"{primary}-{region}"
