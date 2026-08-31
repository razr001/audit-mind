from app.core.language_detection import (
    default_language_for_jurisdiction,
    detect_content_language,
)


def test_detect_content_language_recognizes_chinese_and_english() -> None:
    assert detect_content_language(
        ["个人信息处理者应当采取必要措施，保障个人信息安全。"],
        fallback="en",
    ) == "zh-CN"
    assert detect_content_language(
        ["The controller shall protect personal data and document its safeguards."],
        fallback="zh-CN",
    ) == "en"


def test_detect_content_language_uses_jurisdiction_fallback_for_empty_content() -> None:
    assert detect_content_language([], fallback=default_language_for_jurisdiction("CN")) == "zh-CN"
    assert detect_content_language(["  "], fallback=default_language_for_jurisdiction("EU")) == "en"
