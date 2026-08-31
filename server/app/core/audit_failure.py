"""Public audit failure summaries safe to expose through API responses."""

AUDIT_EXECUTION_FAILED_MESSAGE = (
    "Audit execution failed. Review the source document before retrying."
)
AUDIT_EXECUTION_CANCELLED_MESSAGE = "Audit execution was cancelled."
AUDIT_DISPATCH_FAILED_MESSAGE = "审计任务调度失败，请稍后重试"
AUDIT_RULES_NOT_FOUND_MESSAGE = (
    "No applicable regulation rules were found for this page. Review the rule scope and index."
)
AUDIT_RULES_MAINTAINING_MESSAGE = "规则正在维护，请稍后再试"

_PUBLIC_FAILURE_MESSAGES = frozenset(
    {
        AUDIT_EXECUTION_CANCELLED_MESSAGE,
        AUDIT_DISPATCH_FAILED_MESSAGE,
        AUDIT_EXECUTION_FAILED_MESSAGE,
        AUDIT_RULES_MAINTAINING_MESSAGE,
        AUDIT_RULES_NOT_FOUND_MESSAGE,
    }
)


def public_audit_failure(value: str | None) -> str | None:
    """Map legacy or internal stored exceptions to an allow-listed summary."""
    if value is None:
        return None
    return value if value in _PUBLIC_FAILURE_MESSAGES else AUDIT_EXECUTION_FAILED_MESSAGE
