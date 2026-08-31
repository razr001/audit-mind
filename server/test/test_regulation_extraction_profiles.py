"""规则抽取 Profile 的结构覆盖和 few-shot 契约测试。"""

import pytest

from app.ai.regulation.profiles.base import ExtractionProfile, make_example
from app.ai.regulation.profiles.registry import (
    _EXACT_PROFILES,
    _SOURCE_LANGUAGE_PROFILES,
    get_extraction_profile,
)
from app.models.regulation import RegulationSourceType
from app.models.regulation_rule import RegulationRuleType


def _profiles() -> list[ExtractionProfile]:
    """按名称去重，避免同一 Profile 被 LAW 和 REGULATION 映射重复统计。"""
    by_name = {
        profile.name: profile
        for profile in [
            *_SOURCE_LANGUAGE_PROFILES.values(),
            *_EXACT_PROFILES.values(),
        ]
    }
    return list(by_name.values())


def test_every_profile_covers_all_supported_rule_structures() -> None:
    """每类知识源都必须见过系统支持的全部规则类型。"""
    expected_types = {rule_type.value for rule_type in RegulationRuleType}

    for profile in _profiles():
        covered_types = {
            extraction.attributes["rule_type"]
            for example in profile.examples
            for extraction in example.extractions
            if extraction.attributes is not None
        }
        assert covered_types == expected_types, profile.name


def test_every_profile_teaches_multi_rule_documents_and_ignores_background() -> None:
    """模型应学习多规则拆分；背景过滤由 Prompt 约束而非不兼容的空示例表达。"""
    for profile in _profiles():
        assert any(len(example.extractions) > 1 for example in profile.examples), profile.name
        assert all(example.extractions for example in profile.examples), profile.name
        assert "Ignore titles" in profile.prompt


def test_profile_validation_rejects_extraction_text_not_in_source() -> None:
    """few-shot 改写原文会破坏 grounding，必须在启动时直接拒绝。"""
    invalid_example = make_example(
        text="处理者应当保存日志。",
        extraction_text="处理者必须保存日志",
        rule_type="requirement",
        subject="处理者",
        action="保存日志",
    )

    with pytest.raises(ValueError, match="not copied from source"):
        ExtractionProfile(
            name="invalid.zh",
            prompt="Extract rules.",
            examples=(invalid_example,),
        )


def test_profile_validation_rejects_ungrounded_review_field() -> None:
    """参与审核判断的示例字段不得包含原文不存在的同义改写。"""
    invalid_example = make_example(
        text="处理者应当保存日志。",
        extraction_text="处理者应当保存日志",
        rule_type="requirement",
        subject="处理者",
        action="留存日志",
    )

    with pytest.raises(ValueError, match="not grounded"):
        ExtractionProfile(
            name="invalid.zh",
            prompt="Extract rules.",
            examples=(invalid_example,),
        )


def test_detected_language_without_dedicated_examples_uses_source_default() -> None:
    """自动识别到其他语言时仍可使用通用 Profile，不因用户无法选语言而中断。"""
    profile = get_extraction_profile(
        source_type=RegulationSourceType.REGULATION,
        language="fr",
        jurisdiction="FR",
    )

    assert profile.name == "legal.en"
