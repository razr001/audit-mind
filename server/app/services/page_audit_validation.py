from collections.abc import Sequence

from app.ai.page_audit.schemas import PageAuditFindingOutput
from app.models.regulation_rule import RegulationRule
from app.services.audit_rule_retrieval_service import AuditRuleCandidate
from app.services.page_audit_display_text import sanitize_finding_display_text
from app.services.page_audit_input import AuditInputBlock

ValidatedPageFinding = tuple[
    PageAuditFindingOutput,
    list[AuditInputBlock],
    list[RegulationRule],
]


def validate_and_deduplicate_findings(
    *,
    outputs: Sequence[PageAuditFindingOutput],
    blocks: Sequence[AuditInputBlock],
    candidates: Sequence[AuditRuleCandidate],
) -> list[ValidatedPageFinding]:
    """拒绝模型伪造的引用，并合并同一证据与规则产生的重复发现。"""
    blocks_by_id = {block.id: block for block in blocks}
    rules_by_id = {candidate.rule.id: candidate.rule for candidate in candidates}
    results: list[ValidatedPageFinding] = []
    seen: set[tuple] = set()
    for output in outputs:
        if any(block_id not in blocks_by_id for block_id in output.evidence_block_ids):
            raise RuntimeError("page audit referenced an unknown document block")
        if any(rule_id not in rules_by_id for rule_id in output.rule_ids):
            raise RuntimeError("page audit referenced an unknown regulation rule")
        key = (
            output.level,
            output.title.strip(),
            tuple(sorted(str(value) for value in output.evidence_block_ids)),
            tuple(sorted(str(value) for value in output.rule_ids)),
        )
        if key in seen:
            continue
        seen.add(key)
        document_block_ids = output.evidence_block_ids
        regulation_rule_ids = output.rule_ids
        sanitized_output = output.model_copy(
            update={
                "title": sanitize_finding_display_text(
                    output.title,
                    document_block_ids=document_block_ids,
                    regulation_rule_ids=regulation_rule_ids,
                ),
                "reason": sanitize_finding_display_text(
                    output.reason,
                    document_block_ids=document_block_ids,
                    regulation_rule_ids=regulation_rule_ids,
                ),
                "recommendation": sanitize_finding_display_text(
                    output.recommendation,
                    document_block_ids=document_block_ids,
                    regulation_rule_ids=regulation_rule_ids,
                ),
            }
        )
        results.append(
            (
                sanitized_output,
                [blocks_by_id[value] for value in output.evidence_block_ids],
                [rules_by_id[value] for value in output.rule_ids],
            )
        )
    return results
