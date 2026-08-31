import re

from app.ai.regulation.schemas import ExtractedComplianceRule
from app.models.regulation import Regulation
from app.models.regulation_chunk import RegulationChunk
from app.models.regulation_parse_block import RegulationParseBlock
from app.models.regulation_rule import RegulationRule
from app.services.regulation_rule_source_mapper import RegulationRuleSourceMapper

RULE_EXTRACTOR_VERSION = "1.0"


class RegulationRuleBuilder(RegulationRuleSourceMapper):
    """Validate extracted rules and map their evidence to source blocks."""

    @staticmethod
    def _to_model(
        *,
        regulation: Regulation,
        chunk: RegulationChunk,
        blocks: list[RegulationParseBlock],
        extracted: ExtractedComplianceRule,
        rule_index: int,
    ) -> RegulationRule | None:
        """只接受能由 Chunk 原文还原且具备有效规则类型的结果。"""
        if (
            extracted.rule_type is None
            or chunk.char_start is None
            or not RegulationRuleBuilder._structured_rule_is_complete(extracted)
        ):
            return None
        if (
            extracted.char_start < 0
            or extracted.char_end <= extracted.char_start
            or extracted.char_end > len(chunk.content)
        ):
            return None

        local_interval = RegulationRuleBuilder._resolve_source_interval(
            chunk=chunk,
            blocks=blocks,
            extracted=extracted,
        )
        if local_interval is None:
            return None
        local_start, local_end = local_interval
        source_text = chunk.content[local_start:local_end]
        if not RegulationRuleBuilder._structured_evidence_is_grounded(
            source_text=source_text,
            extracted=extracted,
        ):
            return None

        source_location = RegulationRuleBuilder._resolve_rule_source(
            chunk=chunk,
            blocks=blocks,
            local_start=local_start,
            local_end=local_end,
        )
        if source_location is None:
            return None
        source_blocks, source_segments, global_start, global_end = source_location

        pages = [block.page_number for block in source_blocks if block.page_number is not None]
        payload = {
            "ruleType": extracted.rule_type.value,
            "topic": extracted.topic,
            "subject": extracted.subject,
            "action": extracted.action,
            "object": extracted.object,
            "condition": extracted.condition,
            "timeLimit": extracted.time_limit,
            "requirements": list(extracted.requirements),
            "restrictions": list(extracted.restrictions),
            "exceptions": list(extracted.exceptions),
            "consequences": list(extracted.consequences),
            "provisionReference": extracted.provision_reference,
            "sectionPath": extracted.section_path,
            # 一条清洗后的规则可能跨越多个不连续原始 Block。
            # 这些区间供前端精确高亮；source_char_start/end 仅是外包范围。
            "sourceSegments": source_segments,
        }

        return RegulationRule(
            regulation_id=regulation.id,
            source_chunk_id=chunk.id,
            source_block_ids=[str(block.id) for block in source_blocks],
            rule_index=rule_index,
            rule_type=extracted.rule_type,
            topic=extracted.topic,
            subject=extracted.subject,
            action=extracted.action,
            object=extracted.object,
            condition=extracted.condition,
            time_limit=extracted.time_limit,
            requirements=list(extracted.requirements),
            restrictions=list(extracted.restrictions),
            exceptions=list(extracted.exceptions),
            consequences=list(extracted.consequences),
            payload=payload,
            source_filename=regulation.original_filename,
            source_content_hash=regulation.content_hash,
            source_page_start=min(pages) if pages else None,
            source_page_end=max(pages) if pages else None,
            source_char_start=global_start,
            source_char_end=global_end,
            source_text=source_text,
            extractor_profile=extracted.profile_name or "unknown",
            extractor_version=RULE_EXTRACTOR_VERSION,
        )

    @staticmethod
    def _same_source(left: RegulationRule, right: RegulationRule) -> bool:
        """判断两个候选是否来自同一处原文，而不依赖不稳定的模型属性。"""
        if left.rule_type != right.rule_type:
            return False
        if RegulationRuleBuilder._normalize_source_text(
            left.source_text
        ) != RegulationRuleBuilder._normalize_source_text(right.source_text):
            return False

        # 重叠 Chunk 可能为同一句生成略有差异的外包区间；只要规范原文
        # 相同且全局区间相交，就属于同一来源。相同句子出现在远处仍会保留。
        return max(left.source_char_start, right.source_char_start) < min(
            left.source_char_end,
            right.source_char_end,
        )

    @staticmethod
    def _quality_score(rule: RegulationRule) -> tuple[int, int, int]:
        """同源候选优先保留列表完整、结构字段丰富且信息量更大的一个。"""
        list_items = [
            *rule.requirements,
            *rule.restrictions,
            *rule.exceptions,
            *rule.consequences,
        ]
        scalar_values = [
            rule.topic,
            rule.subject,
            rule.action,
            rule.object,
            rule.condition,
            rule.time_limit,
        ]
        nonempty_scalars = sum(bool(value and value.strip()) for value in scalar_values)
        information_length = sum(len(value) for value in list_items) + sum(
            len(value) for value in scalar_values if value
        )
        return len(list_items), nonempty_scalars, information_length

    @classmethod
    def _merge_same_source(
        cls,
        rules: list[RegulationRule],
        candidate: RegulationRule,
    ) -> bool:
        """合并同源候选；返回 True 表示候选已被合并而非新增。"""
        duplicate_index = next(
            (index for index, rule in enumerate(rules) if cls._same_source(rule, candidate)),
            None,
        )
        if duplicate_index is None:
            return False

        existing = rules[duplicate_index]
        if cls._quality_score(candidate) > cls._quality_score(existing):
            candidate.rule_index = existing.rule_index
            rules[duplicate_index] = candidate
        return True

    @staticmethod
    def _structured_rule_is_complete(extracted: ExtractedComplianceRule) -> bool:
        """拒绝只有“包括/如下”等引导语、却没有结构化明细的残缺规则。"""
        if not extracted.action:
            return True

        normalized_action = re.sub(r"\s+", "", extracted.action).lower().rstrip("：:")
        list_introducer_suffixes = (
            "包括",
            "如下",
            "为",
            "include",
            "includes",
            "including",
            "areasfollows",
            "consistof",
            "consistsof",
            "comprise",
            "comprises",
        )
        introduces_details = normalized_action.endswith(list_introducer_suffixes)
        source_has_list_marker = "：" in extracted.content or ":" in extracted.content
        structured_items = (
            extracted.requirements
            or extracted.restrictions
            or extracted.exceptions
            or extracted.consequences
        )
        return not (introduces_details and source_has_list_marker and not structured_items)

    @staticmethod
    def _normalize_source_text(value: str) -> str:
        """去除 PDF 布局产生的空白，保留文字与标点用于来源判等。"""
        return "".join(value.split())

    @staticmethod
    def _structured_evidence_is_grounded(
        *,
        source_text: str,
        extracted: ExtractedComplianceRule,
    ) -> bool:
        """验证会直接参与审核判断的约束项确实能在规则原文中找到。"""
        # PDF 解析可能在词语之间插入换行或空格，因此比较时仅移除空白；
        # 不做同义改写或标点清洗，防止模型编造近似但不存在的规则。
        normalized_source = "".join(source_text.split())
        grounded_values = [
            value
            for value in (
                extracted.subject,
                extracted.action,
                extracted.object,
                extracted.condition,
            )
            if value
        ]
        grounded_values.extend(
            [
                *extracted.requirements,
                *extracted.restrictions,
                *extracted.exceptions,
                *extracted.consequences,
            ]
        )
        if extracted.time_limit:
            grounded_values.append(extracted.time_limit)
        if not grounded_values:
            return False

        return all(
            "".join(value.split()) in normalized_source
            for value in grounded_values
            if value.strip()
        )
