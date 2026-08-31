import unicodedata


def contains_control_character(value: str) -> bool:
    """Detect Unicode control, format, surrogate and other unsafe code points."""
    return any(unicodedata.category(character).startswith("C") for character in value)


def is_safe_readable_text(value: str) -> bool:
    """Require visible text and reject embedded Unicode Other characters.

    Newlines and horizontal tabs are the only controls allowed in answer text.
    """
    saw_visible_character = False
    for character in value:
        if unicodedata.category(character).startswith("C"):
            if character not in {"\n", "\r", "\t"}:
                return False
        elif not character.isspace():
            saw_visible_character = True
    return saw_visible_character


def require_safe_readable_text(value: str) -> str:
    """供 Pydantic 复用：保留多行文本，拒绝 NUL 等危险控制字符。"""

    if not is_safe_readable_text(value):
        raise ValueError("content must not contain unsafe control characters")
    return value


def has_safe_source_text_characters(value: str, *, multiline: bool) -> bool:
    """Allow PDF format characters while excluding unsafe Cc controls."""
    return all(
        unicodedata.category(character) != "Cc" or (multiline and character in {"\n", "\r", "\t"})
        for character in value
    )


def contains_visible_text(value: str) -> bool:
    """Require a non-whitespace code point outside Unicode Other categories."""
    return any(
        not character.isspace() and not unicodedata.category(character).startswith("C")
        for character in value
    )
