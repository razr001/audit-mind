from uuid import UUID

from fastapi import Depends

from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.repositories.assistant_tool_call_repository import AssistantToolCallRepository
from app.core.error_codes import ASSISTANT_ACTION_INVALID
from app.core.exceptions import BusinessException
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.models.assistant import (
    AssistantAction,
    AssistantActionStatus,
    AssistantToolCall,
    AssistantToolCallStatus,
)
from app.repositories.audit_task_repository import AuditTaskRepository
from app.repositories.regulation_repository import RegulationRepository
from app.schemas.assistant import (
    AssistantActionReconciliationOutcome,
    AssistantActionReconciliationRequest,
)
from app.unit.date import utc_now


class AssistantActionReconciliationService:
    """Resolve uncertain side effects without ever executing the tool again."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        action_repository: AssistantActionRepository,
        tool_call_repository: AssistantToolCallRepository,
        regulation_repository: RegulationRepository,
        audit_task_repository: AuditTaskRepository,
    ) -> None:
        self.uow = uow
        self.action_repository = action_repository
        self.tool_call_repository = tool_call_repository
        self.regulation_repository = regulation_repository
        self.audit_task_repository = audit_task_repository

    async def reconcile(
        self, *, action: AssistantAction, request: AssistantActionReconciliationRequest
    ) -> AssistantAction:
        succeeded = request.outcome != AssistantActionReconciliationOutcome.FAILED
        if succeeded != (request.resource_id is not None):
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "successful reconciliation requires a resource ID; failed reconciliation must omit it",
            )
        resource_type: str | None = None
        if request.resource_id is not None:
            resource_type = await self._verify_resource(
                action=action,
                resource_id=request.resource_id,
            )

        return await self._resolve(
            action=action,
            user_id=action.user_id,
            expected_version=request.version,
            outcome=request.outcome,
            resource_type=resource_type,
            resource_id=request.resource_id,
            note=request.note,
        )

    async def _verify_resource(
        self, *, action: AssistantAction, resource_id: UUID
    ) -> str:
        async with self.uow:
            call = await self.tool_call_repository.find_by_run_and_tool_call(
                run_id=action.run_id,
                tool_call_id=action.tool_call_id,
            )
        if call is None:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "reconciliation tool call does not exist",
            )
        if call.resource_id is not None and call.resource_id != resource_id:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "reconciliation resource does not match the persisted tool receipt",
            )
        if call.status == AssistantToolCallStatus.SUCCEEDED:
            return await self._verify_terminal_receipt_resource(
                action=action,
                resource_id=resource_id,
            )

        if action.tool_name == "create_text_regulation":
            async with self.uow:
                resource = await self.regulation_repository.find_by_agent_tool_call(
                    agent_tool_call_id=call.id,
                    user_id=action.user_id,
                )
            if resource is None or resource.id != resource_id:
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "regulation is not linked to this tool call",
                )
            return "regulation"
        if action.tool_name == "process_regulation":
            if str(resource_id) != str(action.arguments["regulation_id"]):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "regulation does not match the approved action",
                )
            async with self.uow:
                resource = await self.regulation_repository.find_by_id_and_user(
                    regulation_id=resource_id,
                    user_id=action.user_id,
                )
            if resource is None:
                raise BusinessException(ASSISTANT_ACTION_INVALID, "regulation is not owned")
            return "regulation"
        if action.tool_name in {
            "create_markdown_audit",
            "create_document_audit",
        }:
            async with self.uow:
                task = await self.audit_task_repository.find_by_agent_tool_call(
                    agent_tool_call_id=call.id,
                    user_id=action.user_id,
                )
            if task is None or task.id != resource_id:
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "audit task is not linked to this tool call",
                )
            if (
                action.tool_name == "create_document_audit"
                and str(task.document_id) != str(action.arguments["document_id"])
            ):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "audit task document does not match the approved action",
                )
            return "audit_task"
        if action.tool_name == "retry_audit_task":
            if str(resource_id) != str(action.arguments["task_id"]):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "audit task does not match the approved action",
                )
            async with self.uow:
                task = await self.audit_task_repository.find_by_id_and_user(
                    task_id=resource_id,
                    user_id=action.user_id,
                )
            if task is None:
                raise BusinessException(ASSISTANT_ACTION_INVALID, "audit task is not owned")
            return "audit_task"
        raise BusinessException(
            ASSISTANT_ACTION_INVALID,
            "tool does not support reconciliation",
        )

    async def _verify_terminal_receipt_resource(
        self, *, action: AssistantAction, resource_id: UUID
    ) -> str:
        if action.tool_name in {"create_text_regulation", "process_regulation"}:
            async with self.uow:
                resource = await self.regulation_repository.find_by_id_and_user(
                    regulation_id=resource_id,
                    user_id=action.user_id,
                )
            if resource is None:
                raise BusinessException(ASSISTANT_ACTION_INVALID, "regulation is not owned")
            if (
                action.tool_name == "process_regulation"
                and str(resource_id) != str(action.arguments["regulation_id"])
            ):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "regulation does not match the approved action",
                )
            return "regulation"
        if action.tool_name not in {
            "create_markdown_audit",
            "create_document_audit",
            "retry_audit_task",
        }:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "tool does not support reconciliation",
            )
        async with self.uow:
            task = await self.audit_task_repository.find_by_id_and_user(
                task_id=resource_id,
                user_id=action.user_id,
            )
        if task is None:
            raise BusinessException(ASSISTANT_ACTION_INVALID, "audit task is not owned")
        if (
            action.tool_name == "create_document_audit"
            and str(task.document_id) != str(action.arguments["document_id"])
        ):
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "audit task document does not match the approved action",
            )
        if (
            action.tool_name == "retry_audit_task"
            and str(resource_id) != str(action.arguments["task_id"])
        ):
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "audit task does not match the approved action",
            )
        return "audit_task"

    async def _resolve(
        self,
        *,
        action: AssistantAction,
        user_id: UUID,
        expected_version: int,
        outcome: AssistantActionReconciliationOutcome,
        resource_type: str | None,
        resource_id: UUID | None,
        note: str,
    ) -> AssistantAction:
        action_status = AssistantActionStatus(outcome.value)
        tool_status = (
            AssistantToolCallStatus.SUCCEEDED
            if outcome != AssistantActionReconciliationOutcome.FAILED
            else AssistantToolCallStatus.FAILED
        )
        result_code = f"RECONCILED_{outcome.value}"
        now = utc_now()
        async with self.uow:
            locked_action = await self.action_repository.find_owned(
                action_id=action.id,
                user_id=user_id,
                for_update=True,
            )
            if (
                locked_action is None
                or locked_action.status != AssistantActionStatus.RECONCILIATION_REQUIRED
                or locked_action.version != expected_version
            ):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "assistant action is no longer awaiting reconciliation",
                )
            call = await self.tool_call_repository.find_by_run_and_tool_call(
                run_id=action.run_id,
                tool_call_id=action.tool_call_id,
                for_update=True,
            )
            if call is None and outcome != AssistantActionReconciliationOutcome.FAILED:
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "missing tool call can only be reconciled as failed",
                )
            terminal_call = call is None or call.status in {
                AssistantToolCallStatus.SUCCEEDED,
                AssistantToolCallStatus.FAILED,
            }
            self._validate_terminal_receipt(
                call=call,
                outcome=outcome,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            await self._reject_false_failure(
                action=action,
                call=call,
                user_id=user_id,
                outcome=outcome,
            )
            updated = await self.action_repository.resolve_reconciliation(
                action_id=action.id,
                user_id=user_id,
                expected_version=expected_version,
                status=action_status,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                note=note,
                now=now,
            )
            if updated is None:
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "assistant action is no longer awaiting reconciliation",
                )
            tool_updated = terminal_call or await self.tool_call_repository.resolve_reconciliation(
                run_id=action.run_id,
                tool_call_id=action.tool_call_id,
                status=tool_status,
                result_code=result_code,
                resource_type=resource_type,
                resource_id=resource_id,
                completed_at=now,
            )
            if not tool_updated:
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "assistant tool call cannot accept reconciliation",
                )
            return updated

    @staticmethod
    def _validate_terminal_receipt(
        *,
        call: AssistantToolCall | None,
        outcome: AssistantActionReconciliationOutcome,
        resource_type: str | None,
        resource_id: UUID | None,
    ) -> None:
        if call is not None and call.status == AssistantToolCallStatus.SUCCEEDED:
            expected_outcome = (
                AssistantActionReconciliationOutcome.SUCCEEDED
                if call.result_code in {"SUCCEEDED", "ALREADY_COMPLETED"}
                else AssistantActionReconciliationOutcome.PARTIAL
            )
            if (
                outcome != expected_outcome
                or call.resource_type != resource_type
                or call.resource_id != resource_id
            ):
                raise BusinessException(
                    ASSISTANT_ACTION_INVALID,
                    "reconciliation does not match the persisted successful receipt",
                )
        elif (
            call is not None
            and call.status == AssistantToolCallStatus.FAILED
            and outcome != AssistantActionReconciliationOutcome.FAILED
        ):
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "failed tool call can only be reconciled as failed",
            )

    async def _reject_false_failure(
        self,
        *,
        action: AssistantAction,
        call: AssistantToolCall | None,
        user_id: UUID,
        outcome: AssistantActionReconciliationOutcome,
    ) -> None:
        if (
            call is None
            or outcome != AssistantActionReconciliationOutcome.FAILED
            or action.tool_name
            not in {
                "create_text_regulation",
                "create_markdown_audit",
                "create_document_audit",
            }
        ):
            return
        if action.tool_name == "create_text_regulation":
            resource = await self.regulation_repository.find_by_agent_tool_call(
                agent_tool_call_id=call.id,
                user_id=user_id,
            )
        else:
            resource = await self.audit_task_repository.find_by_agent_tool_call(
                agent_tool_call_id=call.id,
                user_id=user_id,
            )
        if resource is not None:
            raise BusinessException(
                ASSISTANT_ACTION_INVALID,
                "created resource exists and cannot be reconciled as failed",
            )


def get_assistant_action_reconciliation_service(
    uow: UnitOfWork = Depends(get_uow),
) -> AssistantActionReconciliationService:
    return AssistantActionReconciliationService(
        uow=uow,
        action_repository=AssistantActionRepository(uow.session),
        tool_call_repository=AssistantToolCallRepository(uow.session),
        regulation_repository=RegulationRepository(uow.session),
        audit_task_repository=AuditTaskRepository(uow.session),
    )
