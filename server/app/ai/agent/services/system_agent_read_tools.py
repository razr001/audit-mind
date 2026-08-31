from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain.tools import tool
from langchain_core.tools import BaseTool

from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.services.document_drafting_service import DocumentDraftingService
from app.ai.agent.tool_result import serialize_tool_result
from app.schemas.audit_task import AuditTaskProgressResponse
from app.schemas.document import DocumentDownloadResponse, DocumentResponse
from app.schemas.regulation import (
    RegulationDetailResponse,
    RegulationParseBlockResponse,
    RegulationPublicResponse,
    RegulationUploadListResponse,
)
from app.schemas.regulation_rule import RegulationRuleResponse
from app.services.audit_workflow_service import AuditWorkflowService
from app.services.document_service import DocumentService
from app.services.regulation_asset_service import RegulationAssetService
from app.services.regulation_detail_service import RegulationDetailService
from app.services.regulation_qa_service import RegulationQaService
from app.services.regulation_rule_orchestrator import RegulationRuleService
from app.services.regulation_service import RegulationService


def build_qa_and_drafting_tools(
    *,
    context: AgentRuntimeContext,
    history: list[dict[str, str]],
    max_chars: int,
    capture_sources: Callable[[list[Any]], None],
    qa_service: RegulationQaService,
    drafting_service: DocumentDraftingService,
) -> list[BaseTool]:
    @tool
    async def answer_regulation_question(question: str) -> str:
        """Answer a regulation or compliance question using verified internal sources."""
        answer = await qa_service.ask(
            user_id=context.user_id, question=question, top_k=5, history=history
        )
        capture_sources(answer.sources)
        return serialize_tool_result(answer, max_chars=max_chars)

    @tool
    async def search_regulations(query: str, top_k: int = 5) -> str:
        """Search regulation knowledge visible to the current user. top_k must be 1 to 10."""
        results = await qa_service.search_service.search(
            user_id=context.user_id, query=query, top_k=max(1, min(top_k, 10))
        )
        items = [
            {
                "regulationId": str(result.regulation_id),
                "title": result.title,
                "jurisdiction": result.jurisdiction,
                "articleNumber": result.article_number,
                "content": result.content,
            }
            for result in results
        ]
        return serialize_tool_result(items, max_chars=max_chars)

    @tool
    async def draft_contract(requirements: str) -> str:
        """Draft a grounded contract with placeholders for facts the user did not provide."""
        draft = await drafting_service.draft_contract(
            user_id=context.user_id, requirements=requirements, history=history
        )
        capture_sources(draft.sources)
        return serialize_tool_result(draft, max_chars=max_chars)

    @tool
    async def review_contract(content: str) -> str:
        """Review contract text against verified regulation knowledge."""
        review = await drafting_service.review_contract(
            user_id=context.user_id, content=content, history=history
        )
        capture_sources(review.sources)
        return serialize_tool_result(review, max_chars=max_chars)

    return [answer_regulation_question, search_regulations, draft_contract, review_contract]


def build_regulation_read_tools(
    *,
    context: AgentRuntimeContext,
    max_chars: int,
    regulation_service: RegulationService,
    detail_service: RegulationDetailService,
    asset_service: RegulationAssetService,
    rule_service: RegulationRuleService,
) -> list[BaseTool]:
    @tool
    async def list_regulations(limit: int = 10) -> str:
        """List regulation knowledge visible to the current user."""
        records, total = await regulation_service.get_accessible_page(
            user_id=context.user_id, offset=0, limit=max(1, min(limit, 20))
        )
        items = [
            RegulationPublicResponse.model_validate(record).model_dump(
                mode="json", by_alias=True
            )
            for record in records
        ]
        return serialize_tool_result({"total": total, "items": items}, max_chars=max_chars)

    @tool
    async def list_my_regulations(limit: int = 10) -> str:
        """List regulation knowledge uploaded by the current user, including pipeline status."""
        records, total = await regulation_service.get_uploaded_page(
            user_id=context.user_id, offset=0, limit=max(1, min(limit, 20))
        )
        items = [
            RegulationUploadListResponse.model_validate(record).model_dump(
                mode="json", by_alias=True
            )
            for record in records
        ]
        return serialize_tool_result({"total": total, "items": items}, max_chars=max_chars)

    @tool
    async def get_regulation_detail(regulation_id: UUID) -> str:
        """Get access-safe details and pipeline status for one regulation."""
        regulation, page_count = await detail_service.get_accessible_detail(
            regulation_id=regulation_id, user_id=context.user_id
        )
        detail = RegulationDetailResponse.model_validate(
            {**vars(regulation), "page_count": page_count}
        )
        detail.can_manage = regulation.uploaded_by == context.user_id
        return serialize_tool_result(detail, max_chars=max_chars)

    @tool
    async def get_regulation_source_download(regulation_id: UUID) -> str:
        """Create a short-lived download URL for an accessible regulation source file."""
        result = await asset_service.create_source_download_url(
            regulation_id=regulation_id, user_id=context.user_id
        )
        return serialize_tool_result(result, max_chars=max_chars)

    @tool
    async def get_regulation_page_blocks(regulation_id: UUID, page_number: int) -> str:
        """Get parsed text and visual blocks for one page of an accessible regulation."""
        blocks = await asset_service.get_page_blocks(
            regulation_id=regulation_id,
            page_number=max(1, page_number),
            user_id=context.user_id,
        )
        items = [
            RegulationParseBlockResponse.model_validate(block).model_dump(
                mode="json", by_alias=True
            )
            for block in blocks
        ]
        return serialize_tool_result(items, max_chars=max_chars)

    @tool
    async def get_regulation_asset_download(block_id: UUID) -> str:
        """Create a short-lived download URL for an accessible regulation image block."""
        result = await asset_service.create_download_url(
            block_id=block_id, user_id=context.user_id
        )
        return serialize_tool_result(result, max_chars=max_chars)

    @tool
    async def get_regulation_rules(regulation_id: UUID, limit: int = 20) -> str:
        """Get structured rules and verified source locations for an accessible regulation."""
        rules, total = await rule_service.get_rules(
            regulation_id=regulation_id,
            user_id=context.user_id,
            offset=0,
            limit=max(1, min(limit, 50)),
            rule_type=None,
        )
        items = [
            RegulationRuleResponse.model_validate(rule).model_dump(mode="json", by_alias=True)
            for rule in rules
        ]
        return serialize_tool_result({"total": total, "items": items}, max_chars=max_chars)

    @tool
    async def count_regulation_rules() -> str:
        """Count all structured regulation rules accessible to the current user."""
        total = await rule_service.count_accessible_rules(user_id=context.user_id)
        return serialize_tool_result({"total": total}, max_chars=max_chars)

    return [
        list_regulations,
        list_my_regulations,
        get_regulation_detail,
        get_regulation_source_download,
        get_regulation_page_blocks,
        get_regulation_asset_download,
        get_regulation_rules,
        count_regulation_rules,
    ]


def build_document_and_audit_read_tools(
    *,
    context: AgentRuntimeContext,
    max_chars: int,
    document_service: DocumentService,
    audit_service: AuditWorkflowService,
) -> list[BaseTool]:
    @tool
    async def list_documents(limit: int = 10) -> str:
        """List documents owned by the current user."""
        records, total = await document_service.get_document_list(
            context.user_id, 0, max(1, min(limit, 20))
        )
        items = [
            DocumentResponse.model_validate(record).model_dump(mode="json", by_alias=True)
            for record in records
        ]
        return serialize_tool_result({"total": total, "items": items}, max_chars=max_chars)

    @tool
    async def get_document(document_id: UUID) -> str:
        """Get one document owned by the current user."""
        document = await document_service.get_document(document_id, context.user_id)
        return serialize_tool_result(
            DocumentResponse.model_validate(document), max_chars=max_chars
        )

    @tool
    async def get_document_download(document_id: UUID) -> str:
        """Create a short-lived download URL for a document owned by the current user."""
        url, expires_in = await document_service.create_download_url(
            document_id=document_id, user_id=context.user_id
        )
        return serialize_tool_result(
            DocumentDownloadResponse(url=url, expires_in=expires_in),
            max_chars=max_chars,
        )

    @tool
    async def list_audit_tasks(limit: int = 10) -> str:
        """List audit tasks owned by the current user."""
        records, total = await audit_service.get_tasks(
            user_id=context.user_id,
            offset=0,
            limit=max(1, min(limit, 20)),
            status=None,
        )
        items = [
            AuditTaskProgressResponse.model_validate(record).model_dump(
                mode="json", by_alias=True
            )
            for record in records
        ]
        return serialize_tool_result({"total": total, "items": items}, max_chars=max_chars)

    @tool
    async def get_audit_task(task_id: UUID) -> str:
        """Get one audit task owned by the current user."""
        task = await audit_service.get_task(task_id=task_id, user_id=context.user_id)
        return serialize_tool_result(
            AuditTaskProgressResponse.model_validate(task), max_chars=max_chars
        )

    @tool
    async def get_audit_page_result(task_id: UUID, page_number: int) -> str:
        """Get one page of validated findings from an owned audit task."""
        result = await audit_service.get_page_result(
            task_id=task_id,
            page_number=max(1, page_number),
            user_id=context.user_id,
        )
        return serialize_tool_result(result, max_chars=max_chars)

    return [
        list_documents,
        get_document,
        get_document_download,
        list_audit_tasks,
        get_audit_task,
        get_audit_page_result,
    ]


def build_read_tools(
    *,
    runtime_context: AgentRuntimeContext,
    history: list[dict[str, str]],
    collected_sources: dict[str, dict],
    max_chars: int,
    regulation_qa_service: RegulationQaService,
    drafting_service: DocumentDraftingService,
    regulation_service: RegulationService,
    regulation_detail_service: RegulationDetailService,
    regulation_asset_service: RegulationAssetService,
    regulation_rule_service: RegulationRuleService,
    document_service: DocumentService,
    audit_service: AuditWorkflowService,
) -> list[BaseTool]:
    """Assemble read tools by business domain."""

    def capture_sources(source_models: list[Any]) -> None:
        for source_model in source_models:
            source = source_model.model_dump(mode="json", by_alias=True)
            key = f"{source.get('chunkId')}:{','.join(source.get('evidenceIds', []))}"
            collected_sources[key] = source

    return [
        *build_qa_and_drafting_tools(
            context=runtime_context,
            history=history,
            max_chars=max_chars,
            capture_sources=capture_sources,
            qa_service=regulation_qa_service,
            drafting_service=drafting_service,
        ),
        *build_regulation_read_tools(
            context=runtime_context,
            max_chars=max_chars,
            regulation_service=regulation_service,
            detail_service=regulation_detail_service,
            asset_service=regulation_asset_service,
            rule_service=regulation_rule_service,
        ),
        *build_document_and_audit_read_tools(
            context=runtime_context,
            max_chars=max_chars,
            document_service=document_service,
            audit_service=audit_service,
        ),
    ]
