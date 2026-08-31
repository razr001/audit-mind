from fastapi import Depends

from app.ai.agent.services.agent_tool_execution_service import (
    AgentToolExecutionService,
    get_agent_tool_execution_service,
)
from app.ai.agent.services.assistant_action_service import (
    AssistantActionService,
    get_assistant_action_service,
)
from app.ai.agent.services.audit_command_service import (
    AuditCommandService,
    get_audit_command_service,
)
from app.ai.agent.services.regulation_command_service import (
    RegulationCommandService,
    get_regulation_command_service,
)
from app.ai.agent.services.system_agent_service import SystemAgentService
from app.infrastructure.db.unit_of_work import UnitOfWork, get_uow
from app.services.audit_workflow_service import AuditWorkflowService, get_audit_workflow_service
from app.services.document_parse_service import DocumentParseService, get_document_parse_service
from app.services.document_service import DocumentService, get_document_service
from app.services.regulation_asset_service import (
    RegulationAssetService,
    get_regulation_asset_service,
)
from app.services.regulation_detail_service import (
    RegulationDetailService,
    get_regulation_detail_service,
)
from app.services.regulation_qa_service import RegulationQaService, get_regulation_qa_service
from app.services.regulation_rule_orchestrator import RegulationRuleService
from app.services.regulation_rule_service import get_regulation_rule_service
from app.services.regulation_service import RegulationService, get_regulation_service


def get_system_agent_service(
    uow: UnitOfWork = Depends(get_uow),
    regulation_qa_service: RegulationQaService = Depends(get_regulation_qa_service),
    regulation_service: RegulationService = Depends(get_regulation_service),
    regulation_detail_service: RegulationDetailService = Depends(get_regulation_detail_service),
    regulation_asset_service: RegulationAssetService = Depends(get_regulation_asset_service),
    regulation_rule_service: RegulationRuleService = Depends(get_regulation_rule_service),
    document_service: DocumentService = Depends(get_document_service),
    document_parse_service: DocumentParseService = Depends(get_document_parse_service),
    audit_service: AuditWorkflowService = Depends(get_audit_workflow_service),
    action_service: AssistantActionService = Depends(get_assistant_action_service),
    audit_command_service: AuditCommandService = Depends(get_audit_command_service),
    regulation_command_service: RegulationCommandService = Depends(get_regulation_command_service),
    tool_execution_service: AgentToolExecutionService = Depends(get_agent_tool_execution_service),
) -> SystemAgentService:
    """Build the system Agent from existing application services."""
    return SystemAgentService(
        uow=uow,
        regulation_qa_service=regulation_qa_service,
        regulation_service=regulation_service,
        regulation_detail_service=regulation_detail_service,
        regulation_asset_service=regulation_asset_service,
        regulation_rule_service=regulation_rule_service,
        document_service=document_service,
        document_parse_service=document_parse_service,
        audit_service=audit_service,
        action_service=action_service,
        audit_command_service=audit_command_service,
        regulation_command_service=regulation_command_service,
        tool_execution_service=tool_execution_service,
    )
