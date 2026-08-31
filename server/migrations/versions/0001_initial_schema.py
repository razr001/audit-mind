"""Create the initial AuditMind database schema.

Revision ID: 0001_initial_schema
Revises:
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INITIAL_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "init.sql"

_TABLES = (
    "app_user",
    "assistant_action",
    "assistant_agent_run",
    "assistant_conversation",
    "assistant_message",
    "assistant_tool_call",
    "audit_task",
    "audit_task_page",
    "document",
    "document_page",
    "document_parse_block",
    "evidence",
    "finding",
    "finding_rule_reference",
    "operation_log",
    "regulation",
    "regulation_chunk",
    "regulation_parse_block",
    "regulation_rule",
)

_ENUM_TYPES = (
    "assistant_action_risk",
    "assistant_action_status",
    "assistant_agent_run_status",
    "assistant_message_role",
    "assistant_message_status",
    "assistant_tool_call_status",
    "auditstage",
    "auditstatus",
    "audittaskpagestatus",
    "documentstatus",
)


def _load_initial_schema_statements() -> list[str]:
    """Load the checked-in SQL file as individual PostgreSQL statements.

    The baseline SQL contains ordinary DDL only. Executing statements one at a
    time keeps it compatible with asyncpg, which rejects multiple commands in
    one prepared statement.
    """

    sql = _INITIAL_SCHEMA_PATH.read_text(encoding="utf-8")
    uncommented_sql = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in uncommented_sql.split(";") if statement.strip()]


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _load_initial_schema_statements():
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    # CASCADE handles foreign-key dependency order while the explicit object
    # lists keep this downgrade scoped to schema objects owned by AuditMind.
    table_names = ", ".join(f"public.{name}" for name in _TABLES)
    op.execute(f"DROP TABLE IF EXISTS {table_names} CASCADE")

    for enum_type in _ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS public.{enum_type}")
