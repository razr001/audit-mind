import json
from typing import Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.services.agent_tool_execution_service import (
    AgentToolExecutionService,
    ToolExecutionResult,
    tool_call_receipt,
)
from app.ai.agent.services.audit_command_service import AuditCommandService
from app.ai.agent.services.command_outcome import CommandOutcome
from app.ai.agent.services.regulation_command_service import RegulationCommandService
from app.models.regulation import KnowledgeVisibility, RegulationSourceType
from app.schemas.regulation import RegulationTextCreateRequest
from app.services.document_parse_service import DocumentParseService


def _request_id(context: AgentRuntimeContext) -> str:
    return context.request_id or f"agent-{context.run_id}"


def _serialize_receipt(
    receipts: list[dict[str, Any]],
    tool_name: str,
    execution: ToolExecutionResult[Any],
) -> str:
    receipt = tool_call_receipt(execution.call)
    if receipt["toolName"] != tool_name:
        raise RuntimeError("tool receipt name does not match the invoked tool")
    receipts.append(receipt)
    return json.dumps(receipt, ensure_ascii=False)


def build_regulation_write_tools(
    *,
    context: AgentRuntimeContext,
    receipts: list[dict[str, Any]],
    command_service: RegulationCommandService,
    execution_service: AgentToolExecutionService,
) -> list[BaseTool]:
    @tool
    async def create_text_regulation(
        title: str,
        content: str,
        jurisdiction: str = "CN",
        source_type: RegulationSourceType = RegulationSourceType.REGULATION,
        visibility: KnowledgeVisibility = KnowledgeVisibility.SHARED,
        *,
        runtime: ToolRuntime[AgentRuntimeContext],
    ) -> str:
        """Create and schedule text regulation knowledge. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        request = RegulationTextCreateRequest(
            title=title,
            content=content,
            jurisdiction=jurisdiction,
            source_type=source_type,
            visibility=visibility,
        )
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=runtime.tool_call_id,
            tool_name="create_text_regulation",
            arguments={
                "title": title,
                "content": content,
                "jurisdiction": jurisdiction,
                "source_type": source_type,
                "visibility": visibility,
            },
            operation=lambda call_id: command_service.create_text_and_process(
                request=request,
                user_id=context.user_id,
                request_id=_request_id(context),
                agent_tool_call_id=call_id,
            ),
            resource_id=lambda value: value.id,
            resource_type="regulation",
        )
        return _serialize_receipt(receipts, "create_text_regulation", execution)

    @tool
    async def process_regulation(
        regulation_id: UUID,
        runtime: ToolRuntime[AgentRuntimeContext],
    ) -> str:
        """Schedule full processing for an owned regulation. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=runtime.tool_call_id,
            tool_name="process_regulation",
            arguments={"regulation_id": regulation_id},
            operation=lambda _call_id: command_service.process(
                regulation_id=regulation_id,
                user_id=context.user_id,
                request_id=_request_id(context),
            ),
            resource_id=lambda value: value.id,
            resource_type="regulation",
        )
        return _serialize_receipt(receipts, "process_regulation", execution)

    return [create_text_regulation, process_regulation]


def build_audit_write_tools(
    *,
    context: AgentRuntimeContext,
    receipts: list[dict[str, Any]],
    command_service: AuditCommandService,
    execution_service: AgentToolExecutionService,
) -> list[BaseTool]:
    @tool
    async def create_markdown_audit(
        title: str,
        content: str,
        rule_scope_json: str | None = None,
        *,
        runtime: ToolRuntime[AgentRuntimeContext],
    ) -> str:
        """Create and schedule an audit from Markdown. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=runtime.tool_call_id,
            tool_name="create_markdown_audit",
            arguments={"title": title, "content": content, "rule_scope_json": rule_scope_json},
            operation=lambda call_id: command_service.create_from_markdown(
                title=title,
                content=content,
                rule_scope_json=rule_scope_json,
                user_id=context.user_id,
                request_id=_request_id(context),
                agent_tool_call_id=call_id,
            ),
            resource_id=lambda value: value.id,
            resource_type="audit_task",
        )
        return _serialize_receipt(receipts, "create_markdown_audit", execution)

    @tool
    async def create_document_audit(
        document_id: UUID,
        rule_scope_json: str | None = None,
        *,
        runtime: ToolRuntime[AgentRuntimeContext],
    ) -> str:
        """Create an audit for an existing owned document. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=runtime.tool_call_id,
            tool_name="create_document_audit",
            arguments={"document_id": document_id, "rule_scope_json": rule_scope_json},
            operation=lambda call_id: command_service.create_from_existing_document(
                document_id=document_id,
                rule_scope_json=rule_scope_json,
                user_id=context.user_id,
                request_id=_request_id(context),
                agent_tool_call_id=call_id,
            ),
            resource_id=lambda value: value.id,
            resource_type="audit_task",
        )
        return _serialize_receipt(receipts, "create_document_audit", execution)

    @tool
    async def retry_audit_task(
        task_id: UUID,
        runtime: ToolRuntime[AgentRuntimeContext],
    ) -> str:
        """Schedule retry for an owned audit task. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=runtime.tool_call_id,
            tool_name="retry_audit_task",
            arguments={"task_id": task_id},
            operation=lambda _call_id: command_service.retry(
                task_id=task_id,
                user_id=context.user_id,
                request_id=_request_id(context),
            ),
            resource_id=lambda value: value.id,
            resource_type="audit_task",
        )
        return _serialize_receipt(receipts, "retry_audit_task", execution)

    return [create_markdown_audit, create_document_audit, retry_audit_task]


def build_document_write_tools(
    *,
    context: AgentRuntimeContext,
    receipts: list[dict[str, Any]],
    parse_service: DocumentParseService,
    execution_service: AgentToolExecutionService,
) -> list[BaseTool]:
    async def parse_operation(document_id: UUID, *, synchronize: bool) -> CommandOutcome[Any]:
        if synchronize:
            document = await parse_service.sync_parse_result(
                document_id=document_id, user_id=context.user_id
            )
        else:
            document = await parse_service.start_parse(
                document_id=document_id, user_id=context.user_id
            )
        return CommandOutcome(document)

    async def execute_parse(
        *, document_id: UUID, tool_name: str, tool_call_id: str, synchronize: bool
    ) -> str:
        execution = await execution_service.execute(
            run_id=context.run_id,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments={"document_id": document_id},
            operation=lambda _call_id: parse_operation(
                document_id, synchronize=synchronize
            ),
            resource_id=lambda value: value.id,
            resource_type="document",
        )
        return _serialize_receipt(receipts, tool_name, execution)

    @tool
    async def start_document_parse(
        document_id: UUID, runtime: ToolRuntime[AgentRuntimeContext]
    ) -> str:
        """Start parsing an owned document. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        return await execute_parse(
            document_id=document_id,
            tool_name="start_document_parse",
            tool_call_id=runtime.tool_call_id,
            synchronize=False,
        )

    @tool
    async def sync_document_parse(
        document_id: UUID, runtime: ToolRuntime[AgentRuntimeContext]
    ) -> str:
        """Synchronize parsing results for an owned document. Always requires approval."""
        if runtime.tool_call_id is None:
            raise RuntimeError("write tool requires a tool call ID")
        return await execute_parse(
            document_id=document_id,
            tool_name="sync_document_parse",
            tool_call_id=runtime.tool_call_id,
            synchronize=True,
        )

    return [start_document_parse, sync_document_parse]


def build_write_tools(
    *,
    runtime_context: AgentRuntimeContext,
    tool_receipts: list[dict[str, Any]],
    audit_command_service: AuditCommandService,
    regulation_command_service: RegulationCommandService,
    document_parse_service: DocumentParseService,
    tool_execution_service: AgentToolExecutionService,
) -> list[BaseTool]:
    """Assemble approval-gated write tools by business domain."""
    return [
        *build_regulation_write_tools(
            context=runtime_context,
            receipts=tool_receipts,
            command_service=regulation_command_service,
            execution_service=tool_execution_service,
        ),
        *build_audit_write_tools(
            context=runtime_context,
            receipts=tool_receipts,
            command_service=audit_command_service,
            execution_service=tool_execution_service,
        ),
        *build_document_write_tools(
            context=runtime_context,
            receipts=tool_receipts,
            parse_service=document_parse_service,
            execution_service=tool_execution_service,
        ),
    ]
