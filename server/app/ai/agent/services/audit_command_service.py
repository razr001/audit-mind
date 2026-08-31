from uuid import UUID

from fastapi import Depends

from app.ai.agent.services.command_outcome import CommandOutcome
from app.models.audit_task import AuditTask
from app.services.audit_workflow_service import AuditWorkflowService, get_audit_workflow_service
from app.services.regulation_availability_service import require_regulation_rules_available
from app.tasks.audit_dispatcher import enqueue_audit_pipeline


class AuditCommandService:
    """Reusable application commands shared by HTTP endpoints and Agent tools."""

    def __init__(self, workflow: AuditWorkflowService) -> None:
        self.workflow = workflow

    async def create_from_markdown(
        self,
        *,
        title: str,
        content: str,
        user_id: UUID,
        request_id: str,
        rule_scope_json: str | None = None,
        agent_tool_call_id: UUID | None = None,
    ) -> CommandOutcome[AuditTask]:
        await require_regulation_rules_available()
        task = await self.workflow.create_from_markdown(
            title=title,
            content=content,
            user_id=user_id,
            rule_scope_json=rule_scope_json,
            agent_tool_call_id=agent_tool_call_id,
        )
        return await self._dispatch(task=task, user_id=user_id, request_id=request_id)

    async def create_from_existing_document(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        request_id: str,
        rule_scope_json: str | None = None,
        agent_tool_call_id: UUID | None = None,
    ) -> CommandOutcome[AuditTask]:
        await require_regulation_rules_available()
        task = await self.workflow.create_from_existing_document(
            document_id=document_id,
            user_id=user_id,
            rule_scope_json=rule_scope_json,
            agent_tool_call_id=agent_tool_call_id,
        )
        return await self._dispatch(task=task, user_id=user_id, request_id=request_id)

    async def retry(
        self,
        *,
        task_id: UUID,
        user_id: UUID,
        request_id: str,
    ) -> CommandOutcome[AuditTask]:
        await require_regulation_rules_available()
        task, should_schedule = await self.workflow.retry_task(
            task_id=task_id,
            user_id=user_id,
        )
        if not should_schedule:
            return CommandOutcome(task, "ALREADY_COMPLETED")
        return await self._dispatch(task=task, user_id=user_id, request_id=request_id)

    async def _dispatch(
        self,
        *,
        task: AuditTask,
        user_id: UUID,
        request_id: str,
    ) -> CommandOutcome[AuditTask]:
        try:
            await enqueue_audit_pipeline(
                task_id=task.id,
                user_id=user_id,
                request_id=request_id,
            )
        except Exception:
            failed = await self.workflow.mark_dispatch_failed(task=task, user_id=user_id)
            return CommandOutcome(failed, "DISPATCH_FAILED")
        return CommandOutcome(task)


def get_audit_command_service(
    workflow: AuditWorkflowService = Depends(get_audit_workflow_service),
) -> AuditCommandService:
    return AuditCommandService(workflow)
