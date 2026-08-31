import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.exceptions import OutputParserException

from app.ai.page_audit.schemas import PageAuditFindingOutput, PageAuditOutput
from app.models.document_parse_block import DocumentParseBlock
from app.models.regulation_rule import RegulationRuleType
from app.repositories.audit_result_repository import AuditResultRepository
from app.services.audit_rule_retrieval_service import AuditRuleCandidate
from app.services.page_audit_input import (
    build_adjacent_context,
    build_audit_batches,
    build_batch_contexts,
)
from app.services.page_audit_rule_snapshot import build_rule_reference
from app.services.page_audit_service import PageAuditService
from app.services.page_audit_validation import validate_and_deduplicate_findings


def make_block(content: str = "默认勾选同意") -> DocumentParseBlock:
    return DocumentParseBlock(
        id=uuid4(),
        document_id=uuid4(),
        block_index=0,
        block_type="text",
        content=content,
        page_number=1,
        bbox=[100, 100, 900, 200],
        char_start=0,
        char_end=len(content),
    )


def make_rule():
    return SimpleNamespace(
        id=uuid4(),
        regulation_id=uuid4(),
        source_chunk_id=uuid4(),
        rule_type=RegulationRuleType.PROHIBITION,
        topic="用户同意",
        subject="个人信息处理者",
        action="不得默认勾选",
        object=None,
        condition=None,
        time_limit=None,
        requirements=[],
        restrictions=["不得默认勾选"],
        exceptions=[],
        consequences=[],
        source_filename="个人信息保护规则.pdf",
        source_content_hash="a" * 64,
        source_page_start=3,
        source_page_end=3,
        source_text="个人信息处理者不得通过默认勾选方式取得同意。",
    )


def test_page_audit_accepts_only_supplied_block_and_rule_ids() -> None:
    block = make_block()
    rule = make_rule()
    output = PageAuditFindingOutput(
        level="HIGH",
        title="默认勾选",
        reason="同意并非用户主动选择",
        recommendation="取消默认选中",
        evidence_block_ids=[block.id],
        rule_ids=[rule.id],
    )

    result = validate_and_deduplicate_findings(
        outputs=[output, output],
        blocks=[block],
        candidates=[AuditRuleCandidate(rule=rule, retrieval_score=0.5)],
    )

    assert len(result) == 1
    assert result[0][1] == [block]
    assert result[0][2] == [rule]


def test_page_audit_removes_internal_reference_ids_from_display_text() -> None:
    block = make_block()
    rule = make_rule()
    unrelated_business_id = uuid4()
    output = PageAuditFindingOutput(
        level="HIGH",
        title=f"文档块“{block.id}”存在问题",
        reason=(
            f"文档块“{block.id}”违反规则“{rule.id}”；"
            f"业务编号 {unrelated_business_id} 应继续保留。"
        ),
        recommendation=f"按照规则“{rule.id}”整改",
        evidence_block_ids=[block.id],
        rule_ids=[rule.id],
    )

    result = validate_and_deduplicate_findings(
        outputs=[output],
        blocks=[block],
        candidates=[AuditRuleCandidate(rule=rule, retrieval_score=0.5)],
    )

    sanitized = result[0][0]
    assert sanitized.title == "文档内容存在问题"
    assert sanitized.reason == f"文档内容违反相关规则；业务编号 {unrelated_business_id} 应继续保留。"
    assert sanitized.recommendation == "按照相关规则整改"


@pytest.mark.parametrize("unknown_kind", ["block", "rule"])
def test_page_audit_rejects_unknown_source_ids(unknown_kind: str) -> None:
    block = make_block()
    rule = make_rule()
    output = PageAuditFindingOutput(
        level="HIGH",
        title="未知引用",
        reason="测试",
        evidence_block_ids=[uuid4() if unknown_kind == "block" else block.id],
        rule_ids=[uuid4() if unknown_kind == "rule" else rule.id],
    )

    with pytest.raises(RuntimeError, match="unknown"):
        validate_and_deduplicate_findings(
            outputs=[output],
            blocks=[block],
            candidates=[AuditRuleCandidate(rule=rule, retrieval_score=0.5)],
        )


def test_page_audit_splits_every_oversized_block_to_enforce_hard_limit() -> None:
    table = make_block("表格" * 20_000)
    table.block_type = "table"

    batches = build_audit_batches([table])

    assert len(batches) > 1
    assert all(sum(len(item.content) for item in batch) <= 2_600 for batch in batches)
    assert all(item.id == table.id for batch in batches for item in batch)


def test_page_audit_batches_reserve_context_within_three_thousand_limit() -> None:
    blocks = [make_block("A" * 1_600), make_block("B" * 1_600), make_block("C" * 100)]

    batches = build_audit_batches(blocks)

    assert [[item.id for item in batch] for batch in batches] == [
        [blocks[0].id],
        [blocks[1].id, blocks[2].id],
    ]


def test_each_audit_batch_uses_only_its_nearest_context() -> None:
    first = make_block("A" * 2_000)
    second = make_block("B" * 2_000)

    contexts = build_batch_contexts(
        batches=[[first], [second]],
        page_context_before="P" * 200,
        page_context_after="N" * 200,
    )

    assert contexts[0] == ("P" * 200, "B" * 200)
    assert contexts[1] == ("A" * 200, "N" * 200)


def test_each_audit_request_document_text_never_exceeds_three_thousand() -> None:
    blocks = [make_block("A" * 8_000)]
    batches = build_audit_batches(blocks)
    contexts = build_batch_contexts(
        batches=batches,
        page_context_before="P" * 200,
        page_context_after="N" * 200,
    )

    for batch, (before, after) in zip(batches, contexts, strict=True):
        assert len(before) + sum(len(item.content) for item in batch) + len(after) <= 3_000


def test_rule_reference_keeps_complete_database_source_snapshot() -> None:
    rule = make_rule()

    reference = build_rule_reference(finding_id=uuid4(), rule=rule)

    assert reference.source_text == rule.source_text
    assert reference.source_content_hash == "a" * 64
    assert reference.rule_snapshot["id"] == str(rule.id)


def test_adjacent_context_uses_only_nearest_200_characters_and_ignores_noise() -> None:
    previous = [
        make_block("A" * 150),
        make_block("B" * 150),
        make_block("页脚内容"),
    ]
    previous[-1].block_type = "footer"
    following = [make_block("C" * 150), make_block("D" * 150)]

    before, after = build_adjacent_context(
        previous_blocks=previous,
        next_blocks=following,
    )

    assert len(before) == 200
    assert before.endswith("B" * 150)
    assert "页脚内容" not in before
    assert len(after) == 200
    assert after.startswith("C" * 150)


def test_page_audit_retries_once_when_provider_omits_required_json_fields() -> None:
    structured_model = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                OutputParserException("level and title are required"),
                PageAuditOutput(findings=[]),
            ]
        )
    )
    model = SimpleNamespace(
        with_structured_output=lambda *_args, **_kwargs: structured_model
    )
    service = PageAuditService(
        uow=SimpleNamespace(),
        result_repository=SimpleNamespace(),
        block_repository=SimpleNamespace(),
        rule_retrieval=SimpleNamespace(),
        progress_service=SimpleNamespace(),
        result_service=SimpleNamespace(),
        model=model,
    )

    result = asyncio.run(
        service._invoke_model(
            task_id=uuid4(),
            page_number=1,
            batch_index=1,
            blocks=build_audit_batches([make_block()])[0],
            candidates=[AuditRuleCandidate(rule=make_rule(), retrieval_score=0.8)],
            context_before="",
            context_after="",
        )
    )

    assert result == []
    assert structured_model.ainvoke.await_count == 2


def test_page_results_flush_findings_before_foreign_key_children() -> None:
    """没有 ORM relationship 时也必须显式保证父记录先写入。"""

    class RecordingSession:
        def __init__(self) -> None:
            self.events: list[tuple[str, object | None]] = []

        async def execute(self, _statement):
            self.events.append(("execute", None))

        def add_all(self, rows) -> None:
            self.events.append(("add_all", rows))

        async def flush(self) -> None:
            self.events.append(("flush", None))

    session = RecordingSession()
    repository = AuditResultRepository(session)  # type: ignore[arg-type]
    findings = [SimpleNamespace(id=uuid4())]
    evidences = [SimpleNamespace(finding_id=findings[0].id)]
    references = [SimpleNamespace(finding_id=findings[0].id)]

    asyncio.run(
        repository.replace_page_findings(
            task_id=uuid4(),
            task_page_id=uuid4(),
            findings=findings,  # type: ignore[arg-type]
            evidences=evidences,  # type: ignore[arg-type]
            rule_references=references,  # type: ignore[arg-type]
        )
    )

    assert session.events[-4:] == [
        ("add_all", findings),
        ("flush", None),
        ("add_all", [*evidences, *references]),
        ("flush", None),
    ]
