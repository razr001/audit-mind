import re
from collections.abc import Iterable
from typing import overload
from uuid import UUID


@overload
def sanitize_finding_display_text(
    value: str,
    *,
    document_block_ids: Iterable[UUID],
    regulation_rule_ids: Iterable[UUID],
) -> str: ...


@overload
def sanitize_finding_display_text(
    value: None,
    *,
    document_block_ids: Iterable[UUID],
    regulation_rule_ids: Iterable[UUID],
) -> None: ...


def sanitize_finding_display_text(
    value: str | None,
    *,
    document_block_ids: Iterable[UUID],
    regulation_rule_ids: Iterable[UUID],
) -> str | None:
    """Remove internal reference IDs from text shown to end users.

    The structured evidence and rule-reference fields retain the UUIDs for
    traceability. Finding prose should refer to their human-readable meaning.
    """

    if value is None:
        return None
    result = value
    for block_id in document_block_ids:
        result = _replace_identifier(
            result,
            identifier=block_id,
            prefixes=("文档块", "证据块", "内容块"),
            replacement="文档内容",
        )
    for rule_id in regulation_rule_ids:
        result = _replace_identifier(
            result,
            identifier=rule_id,
            prefixes=("法规规则", "规则"),
            replacement="相关规则",
        )
    return result


def _replace_identifier(
    value: str,
    *,
    identifier: UUID,
    prefixes: tuple[str, ...],
    replacement: str,
) -> str:
    escaped_id = re.escape(str(identifier))
    prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
    with_label = re.compile(
        rf"(?:{prefix_pattern})\s*[‘’“”'\"「」『』]?\s*{escaped_id}\s*[‘’“”'\"「」『』]?",
        re.IGNORECASE,
    )
    result = with_label.sub(replacement, value)
    return re.sub(escaped_id, replacement, result, flags=re.IGNORECASE)
