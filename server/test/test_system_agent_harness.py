import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.tools import tool

from app.ai.agent.capability_router import classify_agent_intent
from app.ai.agent.checkpointer import AgentCheckpointer
from app.ai.agent.context import AgentRuntimeContext
from app.ai.agent.repositories.assistant_action_repository import AssistantActionRepository
from app.ai.agent.runner import WRITE_TOOL_NAMES
from app.ai.agent.schemas import AgentIntent, SystemAgentFinalOutput
from app.ai.agent.services import system_agent_progress
from app.ai.agent.services.agent_tool_execution_service import AgentToolExecutionService
from app.ai.agent.services.agent_tool_fence import require_running_agent_tool_call
from app.ai.agent.services.assistant_action_reconciliation_service import (
    AssistantActionReconciliationService,
)
from app.ai.agent.services.assistant_action_service import (
    AssistantActionService,
    canonical_action_arguments,
)
from app.ai.agent.services.command_outcome import CommandOutcome
from app.ai.agent.services.document_drafting_service import DocumentDraftingService
from app.ai.agent.services.regulation_command_service import RegulationCommandService
from app.ai.agent.services.system_agent_output_service import (
    decision_final_output,
    validate_agent_final,
)
from app.ai.agent.services.system_agent_read_tools import build_regulation_read_tools
from app.ai.agent.services.system_agent_service import SystemAgentService
from app.ai.agent.services.system_agent_state_service import SystemAgentStateService
from app.ai.agent.tool_registry import normalize_tool_arguments, select_tools
from app.ai.agent.tool_result import serialize_tool_result
from app.ai.regulation_qa.schemas import GuardrailDecision
from app.models.assistant import (
    AssistantAction,
    AssistantActionRisk,
    AssistantActionStatus,
    AssistantAgentRunStatus,
    AssistantToolCall,
    AssistantToolCallStatus,
)
from app.schemas.assistant import (
    AssistantActionDecisionType,
    AssistantActionReconciliationOutcome,
    AssistantActionResponse,
)
from app.server import selector_event_loop_factory


class FakeUnitOfWork:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


async def collect_events(stream) -> list[dict]:
    return [event async for event in stream]


def build_system_agent_service() -> SystemAgentService:
    service = SystemAgentService(
        uow=SimpleNamespace(session=object()),
        regulation_qa_service=SimpleNamespace(
            nodes=SimpleNamespace(
                guardrails=SimpleNamespace(
                    inspect_user_input=AsyncMock(
                        return_value=SimpleNamespace(
                            decision=GuardrailDecision.ALLOW,
                            reason=None,
                        )
                    )
                )
            )
        ),
        regulation_service=SimpleNamespace(),
        regulation_detail_service=SimpleNamespace(),
        regulation_asset_service=SimpleNamespace(),
        regulation_rule_service=SimpleNamespace(),
        document_service=SimpleNamespace(),
        document_parse_service=SimpleNamespace(),
        audit_service=SimpleNamespace(),
        action_service=SimpleNamespace(repository=SimpleNamespace()),
        audit_command_service=SimpleNamespace(),
        regulation_command_service=SimpleNamespace(),
        tool_execution_service=SimpleNamespace(),
    )
    service.state_service = SimpleNamespace(
        save_run=AsyncMock(),
        get_run=AsyncMock(),
        set_running=AsyncMock(),
        record_usage=AsyncMock(),
        finish_run=AsyncMock(),
        interrupt_run=AsyncMock(),
        commit_resume_result=AsyncMock(),
    )
    service._build_tools = lambda *_args, **_kwargs: []
    service._delete_checkpoint = AsyncMock()
    return service


def test_system_agent_stream_executes_core_orchestration() -> None:
    service = build_system_agent_service()
    fake_agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
    final_output = SystemAgentFinalOutput(answer="已查询", answered=True)

    with (
        patch(
            "app.ai.agent.services.system_agent_service.capability_router.classify_agent_intent",
            return_value=AgentIntent.REGULATION_QA,
        ),
        patch(
            "app.ai.agent.services.system_agent_invocation.create_system_agent",
            return_value=fake_agent,
        ),
        patch(
            "app.ai.agent.services.system_agent_service.validate_agent_final",
            new=AsyncMock(return_value=(final_output, [])),
        ),
    ):
        events = asyncio.run(
            collect_events(
                service.stream(
                    user_id=uuid4(),
                    question="查询法规",
                    history=[],
                    conversation_id=uuid4(),
                    assistant_message_id=uuid4(),
                    request_id="request-1",
                )
            )
        )

    assert [event["type"] for event in events] == [
        "phase",
        "phase",
        "text-delta",
        "sources",
        "verified",
        "done",
    ]
    service.state_service.save_run.assert_awaited_once()
    service.state_service.record_usage.assert_awaited_once()
    fake_agent.ainvoke.assert_awaited_once()


@pytest.mark.parametrize("question", ["你能干什么？", "你是谁", "你好"])
def test_safe_conversation_is_answered_by_agent(question: str) -> None:
    service = build_system_agent_service()
    fake_agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
    final_output = SystemAgentFinalOutput(answer="自然对话回答", answered=True)

    with (
        patch(
            "app.ai.agent.services.system_agent_invocation.create_system_agent",
            return_value=fake_agent,
        ),
        patch(
            "app.ai.agent.services.system_agent_service.validate_agent_final",
            new=AsyncMock(return_value=(final_output, [])),
        ),
    ):
        events = asyncio.run(
            collect_events(
                service.stream(
                    user_id=uuid4(),
                    question=question,
                    history=[],
                    conversation_id=uuid4(),
                    assistant_message_id=uuid4(),
                    request_id="request-safe-conversation",
                )
            )
        )

    answer = "".join(
        event["data"]["textDelta"] for event in events if event["type"] == "text-delta"
    )
    assert answer == "自然对话回答"
    service.state_service.save_run.assert_awaited_once()
    fake_agent.ainvoke.assert_awaited_once()


def test_rule_count_question_uses_deterministic_access_controlled_query() -> None:
    service = build_system_agent_service()
    service.regulation_rule_service = SimpleNamespace(
        count_accessible_rules=AsyncMock(return_value=42)
    )
    user_id = uuid4()

    events = asyncio.run(
        collect_events(
            service.stream(
                user_id=user_id,
                question="现在系统有多少条规则",
                history=[],
                conversation_id=uuid4(),
                assistant_message_id=uuid4(),
                request_id="request-rule-count",
            )
        )
    )

    answer = "".join(
        event["data"]["textDelta"] for event in events if event["type"] == "text-delta"
    )
    assert answer == "当前你可访问的结构化法规规则共有 42 条。"
    service.regulation_rule_service.count_accessible_rules.assert_awaited_once_with(
        user_id=user_id
    )
    service.state_service.save_run.assert_not_awaited()


def test_slow_agent_emits_heartbeat_and_is_cancelled_when_stream_closes() -> None:
    service = build_system_agent_service()
    invocation_cancelled = asyncio.Event()

    async def slow_ainvoke(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            invocation_cancelled.set()
            raise

    fake_agent = SimpleNamespace(ainvoke=slow_ainvoke)

    async def exercise() -> list[dict]:
        stream = service.stream(
            user_id=uuid4(),
            question="查询法规",
            history=[],
            conversation_id=uuid4(),
            assistant_message_id=uuid4(),
            request_id="request-heartbeat",
        )
        events = [await anext(stream), await anext(stream), await anext(stream)]
        await stream.aclose()
        return events

    with (
        patch(
            "app.ai.agent.services.system_agent_progress.AGENT_HEARTBEAT_SECONDS",
            0.001,
        ),
        patch(
            "app.ai.agent.services.system_agent_service.capability_router.classify_agent_intent",
            return_value=AgentIntent.REGULATION_QA,
        ),
        patch(
            "app.ai.agent.services.system_agent_invocation.create_system_agent",
            return_value=fake_agent,
        ),
    ):
        events = asyncio.run(exercise())

    assert events[-1] == {"type": "heartbeat", "data": {}}
    assert invocation_cancelled.is_set()
    service.state_service.interrupt_run.assert_awaited_once()


def test_system_agent_resume_stream_executes_rejected_decision() -> None:
    service = build_system_agent_service()
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        thread_id="assistant:thread",
        status=AssistantAgentRunStatus.WAITING_APPROVAL,
        intent=AgentIntent.SYSTEM_WRITE.value,
    )
    action = SimpleNamespace(
        id=uuid4(),
        run_id=run.id,
        user_id=run.user_id,
        tool_call_id="call-1",
        display_summary="创建测试操作",
    )
    service.state_service.get_run.return_value = run
    fake_agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
    final_output = SystemAgentFinalOutput(answer="已取消", answered=True)

    with (
        patch(
            "app.ai.agent.services.system_agent_invocation.create_system_agent",
            return_value=fake_agent,
        ),
        patch(
            "app.ai.agent.services.system_agent_service.validate_agent_final",
            new=AsyncMock(return_value=(final_output, [])),
        ),
    ):
        events = asyncio.run(
            collect_events(
                service.resume_stream(
                    action=action,
                    decision=AssistantActionDecisionType.REJECT,
                )
            )
        )

    assert events[-1]["data"]["answer"] == "已取消"
    service.state_service.set_running.assert_awaited_once_with(run)
    service.state_service.commit_resume_result.assert_awaited_once()
    fake_agent.ainvoke.assert_awaited_once()


def test_interrupted_resume_closes_state_and_deletes_checkpoint() -> None:
    """恢复审批被取消后不能留下可无限重试的 checkpoint。"""

    service = build_system_agent_service()
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        thread_id="assistant:interrupted",
        status=AssistantAgentRunStatus.WAITING_APPROVAL,
        intent=AgentIntent.SYSTEM_WRITE.value,
    )
    action = SimpleNamespace(
        id=uuid4(),
        run_id=run.id,
        user_id=run.user_id,
        tool_call_id="call-interrupted",
        tool_name="create_text_regulation",
    )
    service.state_service.get_run.return_value = run
    service.action_service = SimpleNamespace(
        begin_execution=AsyncMock(return_value=action),
        mark_interrupted=AsyncMock(),
        mark_failed=AsyncMock(),
        mark_reconciliation_required=AsyncMock(),
        get_owned=AsyncMock(),
    )
    service.tool_execution_service = SimpleNamespace(find_call=AsyncMock(return_value=None))

    async def cancelled_ainvoke(*_args, **_kwargs):
        raise asyncio.CancelledError

    fake_agent = SimpleNamespace(ainvoke=cancelled_ainvoke)
    with patch(
        "app.ai.agent.services.system_agent_invocation.create_system_agent",
        return_value=fake_agent,
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                collect_events(
                    service.resume_stream(
                        action=action,
                        decision=AssistantActionDecisionType.APPROVE,
                    )
                )
            )

    service.action_service.mark_interrupted.assert_awaited_once_with(
        action_id=action.id,
        user_id=action.user_id,
    )
    service.state_service.finish_run.assert_awaited_once_with(
        run,
        AssistantAgentRunStatus.FAILED,
        "AGENT_RESUME_INTERRUPTED",
    )
    service._delete_checkpoint.assert_awaited_once_with(run.thread_id)


def test_agent_capacity_limits_concurrent_invocations() -> None:
    """进程内 Agent 数不能超过为 checkpoint 连接池预留的容量。"""

    async def run_test() -> None:
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()

        async def first_invocation():
            first_started.set()
            await release_first.wait()
            return {"messages": []}

        async def second_invocation():
            second_started.set()
            return {"messages": []}

        first = asyncio.create_task(
            collect_events(system_agent_progress.run_agent_with_progress(first_invocation))
        )
        await first_started.wait()
        second = asyncio.create_task(
            collect_events(system_agent_progress.run_agent_with_progress(second_invocation))
        )
        await asyncio.sleep(0.01)
        assert not second_started.is_set()

        release_first.set()
        await first
        await second
        assert second_started.is_set()

    with (
        patch.object(system_agent_progress, "_agent_capacity", asyncio.Semaphore(1)),
        patch.object(system_agent_progress, "AGENT_HEARTBEAT_SECONDS", 0.001),
    ):
        asyncio.run(run_test())


def test_oversized_tool_result_remains_valid_json() -> None:
    serialized = serialize_tool_result({"items": ["法" * 2_000]}, max_chars=1_000)

    payload = json.loads(serialized)
    assert len(serialized) <= 1_000
    assert payload["truncated"] is True
    assert payload["originalCharacters"] > 1_000
    assert isinstance(payload["contentPreview"], str)


def test_small_tool_result_preserves_json_structure() -> None:
    serialized = serialize_tool_result({"items": [{"id": 1}]}, max_chars=1_000)

    assert json.loads(serialized) == {"items": [{"id": 1}]}


def test_output_guard_receives_system_read_intent() -> None:
    inspect_output = AsyncMock(
        return_value=SimpleNamespace(decision=GuardrailDecision.ALLOW)
    )
    qa_service = SimpleNamespace(
        nodes=SimpleNamespace(
            guardrails=SimpleNamespace(inspect_output=inspect_output)
        )
    )
    final_output = SystemAgentFinalOutput(answer="当前共有 0 个审计任务", answered=True)

    with patch(
        "app.ai.agent.services.system_agent_output_service.read_final_output",
        return_value=final_output,
    ):
        result, sources = asyncio.run(
            validate_agent_final(
                regulation_qa_service=qa_service,
                question="查询审计任务数量",
                agent_intent=AgentIntent.SYSTEM_READ,
                agent_result={},
                collected_sources={},
                tool_receipts=[],
            )
        )

    guarded_result = inspect_output.await_args.kwargs["result"]
    assert result is final_output
    assert sources == []
    assert guarded_result["agentIntent"] == AgentIntent.SYSTEM_READ.value
    assert guarded_result["executedTools"] == []


def test_approved_write_output_comes_from_persisted_receipt() -> None:
    action = SimpleNamespace(display_summary="新增法规知识：测试制度")

    output = decision_final_output(
        action=action,
        decision=AssistantActionDecisionType.APPROVE,
        tool_receipt={
            "status": "SUCCEEDED",
            "resultCode": "SUCCEEDED",
            "resourceType": "regulation",
            "resourceId": "resource-1",
        },
    )

    assert output.answer == (
        "新增法规知识：测试制度已执行成功。"
        "法规知识 ID：`resource-1`；结果码：`SUCCEEDED`。"
    )
    assert "等待审批" not in output.answer


def build_reconciliation_service(
    *,
    action_repository,
    tool_call_repository,
    regulation_repository=None,
    audit_task_repository=None,
) -> AssistantActionReconciliationService:
    return AssistantActionReconciliationService(
        uow=FakeUnitOfWork(),
        action_repository=action_repository,
        tool_call_repository=tool_call_repository,
        regulation_repository=regulation_repository or SimpleNamespace(),
        audit_task_repository=audit_task_repository or SimpleNamespace(),
    )


def test_capability_router_separates_drafting_business_writes_and_api_code() -> None:
    assert classify_agent_intent("根据现有法规写一份采购合同") == AgentIntent.DRAFT_LEGAL_DOCUMENT
    assert classify_agent_intent("新增一份公司制度到法规库") == AgentIntent.SYSTEM_WRITE
    assert classify_agent_intent("查询我的审计任务进度") == AgentIntent.SYSTEM_READ
    assert classify_agent_intent("现在系统有多少条规则") == AgentIntent.SYSTEM_READ
    # The deterministic security policy blocks this before routing; routing itself
    # must not accidentally classify it as an authorized system write.
    assert classify_agent_intent("写 Python 调用新增法规 API") == AgentIntent.UNSUPPORTED


def test_windows_api_launcher_provides_selector_event_loop() -> None:
    loop = selector_event_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_system_agent_does_not_force_provider_structured_output() -> None:
    """Thinking models reject the forced tool_choice used by ToolStrategy."""
    sentinel = object()
    with (
        patch("app.ai.agent.runner.create_agent", return_value=sentinel) as factory,
        patch("app.ai.agent.runner.get_chat_model", return_value=SimpleNamespace()),
        patch("app.ai.agent.runner.agent_checkpointer.get", return_value=SimpleNamespace()),
    ):
        from app.ai.agent.runner import create_system_agent

        result = create_system_agent([])

    assert result is sentinel
    assert "response_format" not in factory.call_args.kwargs


def test_agent_checkpointer_uses_connection_pool() -> None:
    pool = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
    saver = SimpleNamespace(setup=AsyncMock(), adelete_thread=AsyncMock())
    checkpointer = AgentCheckpointer()
    loop = selector_event_loop_factory()

    try:
        with (
            patch(
                "app.ai.agent.checkpointer.get_settings",
                return_value=SimpleNamespace(
                    DATABASE_URL="postgresql+asyncpg://user:password@localhost/database"
                ),
            ),
            patch(
                "app.ai.agent.checkpointer.AsyncConnectionPool",
                return_value=pool,
            ) as pool_factory,
            patch(
                "app.ai.agent.checkpointer.AsyncPostgresSaver",
                return_value=saver,
            ) as saver_factory,
        ):
            loop.run_until_complete(checkpointer.initialize())
            loop.run_until_complete(checkpointer.delete_thread("thread-1"))
            loop.run_until_complete(checkpointer.close())
    finally:
        loop.close()

    pool_factory.assert_called_once()
    saver_factory.assert_called_once_with(pool)
    pool.open.assert_awaited_once_with(wait=True)
    saver.setup.assert_awaited_once()
    saver.adelete_thread.assert_awaited_once_with("thread-1")
    pool.close.assert_awaited_once()


def test_dynamic_registry_exposes_only_intent_tools() -> None:
    @tool
    def search_regulations(query: str) -> str:
        """Search regulations."""
        return query

    @tool
    def create_text_regulation(title: str) -> str:
        """Create regulation."""
        return title

    @tool
    def count_regulation_rules() -> str:
        """Count accessible rules."""
        return '{"total":42}'

    tools = [search_regulations, create_text_regulation, count_regulation_rules]

    assert [item.name for item in select_tools(AgentIntent.REGULATION_QA, tools)] == [
        "search_regulations"
    ]
    assert [item.name for item in select_tools(AgentIntent.SYSTEM_WRITE, tools)] == [
        "search_regulations",
        "create_text_regulation",
        "count_regulation_rules",
    ]
    assert [item.name for item in select_tools(AgentIntent.SYSTEM_READ, tools)] == [
        "search_regulations",
        "count_regulation_rules",
    ]


def test_rule_count_tool_returns_access_controlled_total() -> None:
    user_id = uuid4()
    rule_service = SimpleNamespace(count_accessible_rules=AsyncMock(return_value=42))
    tools = build_regulation_read_tools(
        context=AgentRuntimeContext(
            user_id=user_id,
            conversation_id=uuid4(),
            run_id=uuid4(),
            request_id="request-count-rules",
        ),
        max_chars=1_000,
        regulation_service=SimpleNamespace(),
        detail_service=SimpleNamespace(),
        asset_service=SimpleNamespace(),
        rule_service=rule_service,
    )
    count_tool = next(item for item in tools if item.name == "count_regulation_rules")

    result = json.loads(asyncio.run(count_tool.ainvoke({})))

    assert result == {"total": 42}
    rule_service.count_accessible_rules.assert_awaited_once_with(user_id=user_id)


def test_action_argument_hash_is_canonical_and_sensitive_to_changes() -> None:
    first, first_hash = canonical_action_arguments({"title": "制度", "content": "正文"})
    second, second_hash = canonical_action_arguments({"content": "正文", "title": "制度"})
    _, changed_hash = canonical_action_arguments({"title": "制度", "content": "另一正文"})

    assert first == second
    assert first_hash == second_hash
    assert changed_hash != first_hash


def test_document_drafting_forces_placeholders_and_grounding() -> None:
    regulation_qa_service = SimpleNamespace(ask=AsyncMock(return_value=SimpleNamespace()))
    drafting_service = DocumentDraftingService(regulation_qa_service)

    asyncio.run(
        drafting_service.draft_contract(
            user_id=uuid4(),
            requirements="起草采购合同",
        )
    )

    question = regulation_qa_service.ask.await_args.kwargs["question"]
    assert "【待填写】" in question
    assert "不得编造法规依据" in question
    assert "起草采购合同" in question


def test_write_tool_schema_hides_runtime_identity_and_tool_call_id() -> None:
    uow = SimpleNamespace(session=object())
    system_agent_service = SystemAgentService(
        uow=uow,
        regulation_qa_service=SimpleNamespace(),
        regulation_service=SimpleNamespace(),
        regulation_detail_service=SimpleNamespace(),
        regulation_asset_service=SimpleNamespace(),
        regulation_rule_service=SimpleNamespace(),
        document_service=SimpleNamespace(),
        document_parse_service=SimpleNamespace(),
        audit_service=SimpleNamespace(),
        action_service=SimpleNamespace(repository=SimpleNamespace()),
        audit_command_service=SimpleNamespace(),
        regulation_command_service=SimpleNamespace(),
        tool_execution_service=SimpleNamespace(),
    )
    runtime_context = SimpleNamespace(
        user_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
        request_id="request-id",
    )

    tools = system_agent_service._build_tools(
        runtime_context=runtime_context,
        history=[],
        collected_sources={},
        tool_receipts=[],
    )
    assert {
        "get_regulation_detail",
        "get_regulation_source_download",
        "get_regulation_page_blocks",
        "get_regulation_asset_download",
        "get_regulation_rules",
        "get_document_download",
        "start_document_parse",
        "sync_document_parse",
    }.issubset({item.name for item in tools})
    assert set(WRITE_TOOL_NAMES) == {
        "create_text_regulation",
        "process_regulation",
        "create_markdown_audit",
        "create_document_audit",
        "retry_audit_task",
        "start_document_parse",
        "sync_document_parse",
    }
    selected_write_names = {
        item.name for item in select_tools(AgentIntent.SYSTEM_WRITE, tools)
    }
    assert set(WRITE_TOOL_NAMES).issubset(selected_write_names)
    write_tool = next(item for item in tools if item.name == "create_text_regulation")
    properties = write_tool.tool_call_schema.model_json_schema()["properties"]

    assert "runtime" not in properties
    assert "user_id" not in properties
    assert "run_id" not in properties


def test_write_tool_approval_materializes_execution_defaults() -> None:
    system_agent_service = SystemAgentService(
        uow=SimpleNamespace(session=object()),
        regulation_qa_service=SimpleNamespace(),
        regulation_service=SimpleNamespace(),
        regulation_detail_service=SimpleNamespace(),
        regulation_asset_service=SimpleNamespace(),
        regulation_rule_service=SimpleNamespace(),
        document_service=SimpleNamespace(),
        document_parse_service=SimpleNamespace(),
        audit_service=SimpleNamespace(),
        action_service=SimpleNamespace(repository=SimpleNamespace()),
        audit_command_service=SimpleNamespace(),
        regulation_command_service=SimpleNamespace(),
        tool_execution_service=SimpleNamespace(),
    )
    runtime_context = SimpleNamespace(
        user_id=uuid4(),
        conversation_id=uuid4(),
        run_id=uuid4(),
        request_id="request-id",
    )
    tools = system_agent_service._build_tools(runtime_context, [], {}, [])

    arguments = normalize_tool_arguments(
        tools,
        tool_name="create_text_regulation",
        arguments={"title": "制度", "content": "正文"},
    )

    assert arguments == {
        "title": "制度",
        "content": "正文",
        "jurisdiction": "CN",
        "source_type": "REGULATION",
        "visibility": "SHARED",
    }


def test_action_response_exposes_frozen_arguments_and_hash() -> None:
    arguments, arguments_hash = canonical_action_arguments({"title": "制度", "content": "正文"})
    action = AssistantAction(
        id=uuid4(),
        run_id=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        tool_call_id="call-1",
        tool_name="create_text_regulation",
        risk_level=AssistantActionRisk.WRITE,
        arguments=arguments,
        arguments_hash=arguments_hash,
        display_summary="新增法规知识：制度",
        status=AssistantActionStatus.PENDING,
        version=1,
        expires_at=datetime.now(timezone.utc),
    )

    response = AssistantActionResponse.model_validate(action)

    assert response.arguments == arguments
    assert response.arguments_hash == arguments_hash


def test_approve_retry_is_idempotent_only_for_same_frozen_hash() -> None:
    _, arguments_hash = canonical_action_arguments({"task_id": str(uuid4())})
    action = SimpleNamespace(
        status=AssistantActionStatus.EXECUTING,
        version=2,
        arguments_hash=arguments_hash,
    )
    repository = SimpleNamespace(
        decide=AsyncMock(return_value=None),
        find_owned=AsyncMock(return_value=action),
    )
    service = AssistantActionService(uow=FakeUnitOfWork(), repository=repository)

    result = asyncio.run(
        service.decide(
            action_id=uuid4(),
            user_id=uuid4(),
            expected_version=1,
            decision=AssistantActionDecisionType.APPROVE,
            arguments_hash=arguments_hash,
        )
    )

    assert result is action


def test_succeeded_tool_call_reuses_receipt_without_repeating_operation() -> None:
    resource_id = uuid4()
    arguments = {"task_id": str(uuid4())}
    _, arguments_hash = canonical_action_arguments(arguments)
    existing = AssistantToolCall(
        run_id=uuid4(),
        tool_call_id="call-1",
        tool_name="retry_audit_task",
        arguments_hash=arguments_hash,
        idempotency_key="b" * 64,
        status=AssistantToolCallStatus.SUCCEEDED,
        result_code="SUCCEEDED",
        resource_type="audit_task",
        resource_id=resource_id,
    )
    repository = SimpleNamespace(find_by_idempotency_key=AsyncMock(return_value=existing))
    action_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                tool_name=existing.tool_name,
                arguments_hash=arguments_hash,
            )
        )
    )
    service = AgentToolExecutionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        action_repository=action_repository,
    )
    operation = AsyncMock()

    result = asyncio.run(
        service.execute(
            run_id=existing.run_id,
            user_id=uuid4(),
            conversation_id=uuid4(),
            tool_call_id=existing.tool_call_id,
            tool_name=existing.tool_name,
            arguments=arguments,
            operation=operation,
            resource_id=lambda value: value.id,
            resource_type="audit_task",
        )
    )

    assert result.value is None
    assert result.call.resource_id == resource_id
    operation.assert_not_awaited()


def test_regulation_creation_reports_dispatch_failure_as_partial_outcome() -> None:
    regulation = SimpleNamespace(
        id=uuid4(),
        status="READY",
        chunk_status="PENDING",
        index_status="PENDING",
        rule_status="PENDING",
    )
    text_service = SimpleNamespace(create=AsyncMock(return_value=regulation))
    service = RegulationCommandService(text_service)

    with patch(
        "app.ai.agent.services.regulation_command_service.schedule_regulation_pipeline",
        new=AsyncMock(side_effect=ConnectionError("redis unavailable")),
    ):
        outcome = asyncio.run(
            service.create_text_and_process(
                request=SimpleNamespace(),
                user_id=uuid4(),
                request_id="request-1",
            )
        )

    assert outcome.resource is regulation
    assert outcome.result_code == "DISPATCH_FAILED"
    assert outcome.is_partial is True


def test_cancelled_side_effect_moves_tool_call_to_reconciliation() -> None:
    document_id = uuid4()
    _, arguments_hash = canonical_action_arguments({"document_id": document_id})
    call = AssistantToolCall(
        run_id=uuid4(),
        tool_call_id="call-uncertain",
        tool_name="create_document_audit",
        arguments_hash=arguments_hash,
        idempotency_key="b" * 64,
        status=AssistantToolCallStatus.RUNNING,
        retry_count=0,
    )
    repository = SimpleNamespace(
        find_by_idempotency_key=AsyncMock(return_value=None),
        save=AsyncMock(return_value=call),
    )

    async def finish_running(**kwargs):
        call.status = kwargs["status"]
        call.result_code = kwargs["result_code"]
        return True

    repository.finish_running = AsyncMock(side_effect=finish_running)
    action_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                tool_name=call.tool_name,
                arguments_hash=arguments_hash,
            )
        )
    )
    service = AgentToolExecutionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        action_repository=action_repository,
    )

    async def cancelled_operation(_call_id):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.execute(
                run_id=call.run_id,
                user_id=uuid4(),
                conversation_id=uuid4(),
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                arguments={"document_id": document_id},
                operation=cancelled_operation,
                resource_id=lambda value: value.id,
                resource_type="audit_task",
            )
        )

    assert call.status == AssistantToolCallStatus.RECONCILIATION_REQUIRED
    assert call.result_code == "INTERRUPTED_SIDE_EFFECT_UNCERTAIN"
    action_lookup = action_repository.find_by_run_and_tool_call.await_args.kwargs
    assert action_lookup["user_id"] is not None
    assert action_lookup["conversation_id"] is not None
    assert action_lookup["status"] == AssistantActionStatus.EXECUTING
    assert action_lookup["for_update"] is True


def test_tool_execution_rejects_arguments_that_differ_from_approval() -> None:
    run_id = uuid4()
    tool_call_id = "call-mismatch"
    _, approved_hash = canonical_action_arguments(
        {"title": "制度", "content": "正文", "visibility": "PRIVATE"}
    )
    action_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                tool_name="create_text_regulation",
                arguments_hash=approved_hash,
            )
        )
    )
    repository = SimpleNamespace(find_by_idempotency_key=AsyncMock())
    service = AgentToolExecutionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        action_repository=action_repository,
    )

    with pytest.raises(Exception, match="does not match"):
        asyncio.run(
            service.execute(
                run_id=run_id,
                user_id=uuid4(),
                conversation_id=uuid4(),
                tool_call_id=tool_call_id,
                tool_name="create_text_regulation",
                arguments={"title": "制度", "content": "正文", "visibility": "SHARED"},
                operation=AsyncMock(),
                resource_id=lambda value: value.id,
                resource_type="regulation",
            )
        )
    repository.find_by_idempotency_key.assert_not_awaited()


def test_reconciliation_closes_action_and_tool_call_atomically() -> None:
    action = SimpleNamespace(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-reconcile",
    )
    reconciled = SimpleNamespace(status=AssistantActionStatus.SUCCEEDED)
    repository = SimpleNamespace(
        find_owned=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                version=3,
            )
        ),
        resolve_reconciliation=AsyncMock(return_value=reconciled),
    )
    tool_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
                result_code="INTERRUPTED_SIDE_EFFECT_UNCERTAIN",
            )
        ),
        resolve_reconciliation=AsyncMock(return_value=True),
    )
    service = build_reconciliation_service(
        action_repository=repository,
        tool_call_repository=tool_repository,
    )
    user_id = uuid4()
    resource_id = uuid4()

    result = asyncio.run(
        service._resolve(
            action=action,
            user_id=user_id,
            expected_version=3,
            outcome=AssistantActionReconciliationOutcome.SUCCEEDED,
            resource_type="regulation",
            resource_id=resource_id,
            note="已核对法规详情页和操作日志",
        )
    )

    assert result is reconciled
    assert repository.resolve_reconciliation.await_args.kwargs["status"] == (
        AssistantActionStatus.SUCCEEDED
    )
    assert tool_repository.resolve_reconciliation.await_args.kwargs["status"] == (
        AssistantToolCallStatus.SUCCEEDED
    )
    assert tool_repository.resolve_reconciliation.await_args.kwargs["resource_id"] == resource_id


def test_successful_tool_receipt_can_close_legacy_reconciliation_without_rewrite() -> None:
    resource_id = uuid4()
    action = SimpleNamespace(id=uuid4(), run_id=uuid4(), tool_call_id="call-complete")
    reconciled = SimpleNamespace(status=AssistantActionStatus.SUCCEEDED)
    repository = SimpleNamespace(
        find_owned=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                version=2,
            )
        ),
        resolve_reconciliation=AsyncMock(return_value=reconciled),
    )
    tool_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantToolCallStatus.SUCCEEDED,
                result_code="SUCCEEDED",
                resource_type="regulation",
                resource_id=resource_id,
            )
        ),
        resolve_reconciliation=AsyncMock(),
    )
    service = build_reconciliation_service(
        action_repository=repository,
        tool_call_repository=tool_repository,
    )

    result = asyncio.run(
        service._resolve(
            action=action,
            user_id=uuid4(),
            expected_version=2,
            outcome=AssistantActionReconciliationOutcome.SUCCEEDED,
            resource_type="regulation",
            resource_id=resource_id,
            note="已核对持久化执行凭证",
        )
    )

    assert result is reconciled
    tool_repository.resolve_reconciliation.assert_not_awaited()


def test_pending_lookup_recovers_stale_execution_before_returning_actions() -> None:
    repository = SimpleNamespace(
        expire_pending=AsyncMock(return_value=0),
        find_active_for_conversation=AsyncMock(return_value=[]),
    )
    reconciliation_repository = SimpleNamespace(recover_stale_executions=AsyncMock(return_value=1))
    service = AssistantActionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        reconciliation_repository=reconciliation_repository,
    )
    conversation_id = uuid4()
    user_id = uuid4()

    actions = asyncio.run(service.list_active(conversation_id=conversation_id, user_id=user_id))

    assert actions == []
    recovered = reconciliation_repository.recover_stale_executions.await_args.kwargs
    assert recovered["conversation_id"] == conversation_id
    assert recovered["user_id"] == user_id
    assert recovered["stale_before"] < recovered["now"]


def test_expiring_approval_also_closes_waiting_assistant_message() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(),
                SimpleNamespace(rowcount=1),
            ]
        )
    )
    repository = AssistantActionRepository(session)

    expired_count = asyncio.run(
        repository.expire_pending(
            conversation_id=uuid4(),
            user_id=uuid4(),
            now=datetime.now(timezone.utc),
        )
    )

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert expired_count == 1
    assert "UPDATE assistant_message" in statements[0]
    assert "UPDATE assistant_agent_run" in statements[1]
    assert "UPDATE assistant_action" in statements[2]


def test_stale_action_without_tool_call_can_close_as_failed() -> None:
    action = SimpleNamespace(id=uuid4(), run_id=uuid4(), tool_call_id="not-started")
    reconciled = SimpleNamespace(status=AssistantActionStatus.FAILED)
    repository = SimpleNamespace(
        find_owned=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                version=3,
            )
        ),
        resolve_reconciliation=AsyncMock(return_value=reconciled),
    )
    tool_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(return_value=None),
        resolve_reconciliation=AsyncMock(),
    )
    service = build_reconciliation_service(
        action_repository=repository,
        tool_call_repository=tool_repository,
    )

    result = asyncio.run(
        service._resolve(
            action=action,
            user_id=uuid4(),
            expected_version=3,
            outcome=AssistantActionReconciliationOutcome.FAILED,
            resource_type=None,
            resource_id=None,
            note="执行栅栏记录不存在，确认副作用尚未开始",
        )
    )

    assert result is reconciled
    tool_repository.resolve_reconciliation.assert_not_awaited()


def test_failed_reconciliation_cannot_hide_an_agent_created_resource() -> None:
    action = SimpleNamespace(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="created-before-crash",
        tool_name="create_text_regulation",
    )
    call = SimpleNamespace(
        id=uuid4(),
        status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
        result_code="STALE_SIDE_EFFECT_UNCERTAIN",
    )
    repository = SimpleNamespace(
        find_owned=AsyncMock(
            return_value=SimpleNamespace(
                status=AssistantActionStatus.RECONCILIATION_REQUIRED,
                version=3,
            )
        ),
        resolve_reconciliation=AsyncMock(),
    )
    tool_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(return_value=call),
        resolve_reconciliation=AsyncMock(),
    )
    regulation_repository = SimpleNamespace(
        find_by_agent_tool_call=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    service = build_reconciliation_service(
        action_repository=repository,
        tool_call_repository=tool_repository,
        regulation_repository=regulation_repository,
    )

    with pytest.raises(Exception, match="cannot be reconciled as failed"):
        asyncio.run(
            service._resolve(
                action=action,
                user_id=uuid4(),
                expected_version=3,
                outcome=AssistantActionReconciliationOutcome.FAILED,
                resource_type=None,
                resource_id=None,
                note="未发现结果",
            )
        )
    assert tool_repository.find_by_run_and_tool_call.await_args.kwargs["for_update"] is True
    repository.resolve_reconciliation.assert_not_awaited()


def test_resource_commit_fence_rejects_a_reconciled_tool_call() -> None:
    call = SimpleNamespace(status=AssistantToolCallStatus.RECONCILIATION_REQUIRED)
    result = SimpleNamespace(scalar_one_or_none=lambda: call)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(Exception, match="no longer active"):
        asyncio.run(require_running_agent_tool_call(session, uuid4()))


def test_late_tool_completion_cannot_overwrite_reconciliation() -> None:
    arguments = {"task_id": str(uuid4())}
    _, arguments_hash = canonical_action_arguments(arguments)
    call = AssistantToolCall(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="late-completion",
        tool_name="retry_audit_task",
        arguments_hash=arguments_hash,
        idempotency_key="c" * 64,
        status=AssistantToolCallStatus.RUNNING,
    )
    repository = SimpleNamespace(
        find_by_idempotency_key=AsyncMock(return_value=None),
        save=AsyncMock(return_value=call),
        complete_running=AsyncMock(return_value=None),
    )
    action_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                tool_name=call.tool_name,
                arguments_hash=arguments_hash,
            )
        )
    )
    service = AgentToolExecutionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        action_repository=action_repository,
    )

    with pytest.raises(Exception, match="lost its fencing token"):
        asyncio.run(
            service.execute(
                run_id=call.run_id,
                user_id=uuid4(),
                conversation_id=uuid4(),
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                arguments=arguments,
                operation=AsyncMock(return_value=CommandOutcome(SimpleNamespace(id=uuid4()))),
                resource_id=lambda value: value.id,
                resource_type="audit_task",
            )
        )


def test_pause_for_approval_updates_message_action_and_run_in_one_uow() -> None:
    uow = FakeUnitOfWork()
    uow.session = object()
    action_repository = SimpleNamespace(save=AsyncMock())
    state = SystemAgentStateService(
        unit_of_work=uow,
        action_repository=action_repository,
    )
    state.assistant_repository = SimpleNamespace(
        pause_generating_message=AsyncMock(return_value=True)
    )
    state.run_repository = SimpleNamespace(set_status=AsyncMock())
    run = SimpleNamespace(id=uuid4(), user_id=uuid4(), assistant_message_id=uuid4())
    action = SimpleNamespace()

    asyncio.run(state.pause_for_approval(run, action))

    action_repository.save.assert_awaited_once_with(action)
    state.assistant_repository.pause_generating_message.assert_awaited_once_with(
        run.assistant_message_id
    )
    assert state.run_repository.set_status.await_args.kwargs["status"].value == ("WAITING_APPROVAL")


def test_new_tool_resource_receives_persisted_tool_call_id() -> None:
    arguments = {"title": "制度", "content": "正文"}
    _, arguments_hash = canonical_action_arguments(arguments)
    call = AssistantToolCall(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-provenance",
        tool_name="create_text_regulation",
        arguments_hash=arguments_hash,
        idempotency_key="b" * 64,
        status=AssistantToolCallStatus.RUNNING,
    )
    repository = SimpleNamespace(
        find_by_idempotency_key=AsyncMock(return_value=None),
        save=AsyncMock(return_value=call),
        complete_running=AsyncMock(return_value=call),
    )
    action_repository = SimpleNamespace(
        find_by_run_and_tool_call=AsyncMock(
            return_value=SimpleNamespace(
                tool_name=call.tool_name,
                arguments_hash=arguments_hash,
            )
        )
    )
    operation = AsyncMock(return_value=CommandOutcome(SimpleNamespace(id=uuid4())))
    service = AgentToolExecutionService(
        uow=FakeUnitOfWork(),
        repository=repository,
        action_repository=action_repository,
    )

    asyncio.run(
        service.execute(
            run_id=call.run_id,
            user_id=uuid4(),
            conversation_id=uuid4(),
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            arguments=arguments,
            operation=operation,
            resource_id=lambda value: value.id,
            resource_type="regulation",
        )
    )

    operation.assert_awaited_once_with(call.id)


def test_created_resource_reconciliation_rejects_unlinked_resource() -> None:
    resource_id = uuid4()
    call = SimpleNamespace(
        id=uuid4(),
        resource_id=None,
        status=AssistantToolCallStatus.RECONCILIATION_REQUIRED,
    )
    action = SimpleNamespace(
        run_id=uuid4(),
        tool_call_id="call-unlinked",
        tool_name="create_text_regulation",
        user_id=uuid4(),
        arguments={},
    )
    tool_repository = SimpleNamespace(find_by_run_and_tool_call=AsyncMock(return_value=call))
    regulation_repository = SimpleNamespace(
        find_by_agent_tool_call=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    service = build_reconciliation_service(
        action_repository=SimpleNamespace(),
        tool_call_repository=tool_repository,
        regulation_repository=regulation_repository,
    )

    with pytest.raises(Exception, match="not linked"):
        asyncio.run(service._verify_resource(action=action, resource_id=resource_id))
