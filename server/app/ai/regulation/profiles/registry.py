from app.ai.regulation.profiles.base import ExtractionProfile
from app.ai.regulation.profiles.contract import (
    CONTRACT_EN_PROFILE,
    CONTRACT_ZH_PROFILE,
)
from app.ai.regulation.profiles.custom import (
    CUSTOM_EN_PROFILE,
    CUSTOM_ZH_PROFILE,
)
from app.ai.regulation.profiles.internal import (
    INTERNAL_EN_PROFILE,
    INTERNAL_ZH_PROFILE,
)
from app.ai.regulation.profiles.legal import (
    LEGAL_EN_PROFILE,
    LEGAL_EU_EN_PROFILE,
    LEGAL_ZH_PROFILE,
)
from app.ai.regulation.profiles.platform import (
    PLATFORM_EN_PROFILE,
    PLATFORM_ZH_PROFILE,
)
from app.ai.regulation.profiles.standard import (
    STANDARD_EN_PROFILE,
    STANDARD_ZH_PROFILE,
)
from app.models.regulation import RegulationSourceType

SUPPORTED_EXTRACTION_LANGUAGES = frozenset({"zh", "en"})
EU_JURISDICTION_ALIASES = frozenset({"EU", "EUROPEAN UNION", "欧盟"})


_EXACT_PROFILES: dict[
    tuple[RegulationSourceType, str, str | None],
    ExtractionProfile,
] = {
    (RegulationSourceType.LAW, "en", "EU"): LEGAL_EU_EN_PROFILE,
    (RegulationSourceType.REGULATION, "en", "EU"): LEGAL_EU_EN_PROFILE,
}

_SOURCE_LANGUAGE_PROFILES: dict[
    tuple[RegulationSourceType, str],
    ExtractionProfile,
] = {
    (RegulationSourceType.LAW, "zh"): LEGAL_ZH_PROFILE,
    (RegulationSourceType.REGULATION, "zh"): LEGAL_ZH_PROFILE,
    (RegulationSourceType.INDUSTRY_STANDARD, "zh"): STANDARD_ZH_PROFILE,
    (RegulationSourceType.INDUSTRY_STANDARD, "en"): STANDARD_EN_PROFILE,
    (RegulationSourceType.PLATFORM_POLICY, "zh"): PLATFORM_ZH_PROFILE,
    (RegulationSourceType.INTERNAL_POLICY, "zh"): INTERNAL_ZH_PROFILE,
    (RegulationSourceType.INTERNAL_POLICY, "en"): INTERNAL_EN_PROFILE,
    (RegulationSourceType.CONTRACT, "zh"): CONTRACT_ZH_PROFILE,
    (RegulationSourceType.CONTRACT, "en"): CONTRACT_EN_PROFILE,
    (RegulationSourceType.CUSTOM_RULE, "zh"): CUSTOM_ZH_PROFILE,
    (RegulationSourceType.LAW, "en"): LEGAL_EN_PROFILE,
    (RegulationSourceType.REGULATION, "en"): LEGAL_EN_PROFILE,
    (RegulationSourceType.PLATFORM_POLICY, "en"): PLATFORM_EN_PROFILE,
    (RegulationSourceType.CUSTOM_RULE, "en"): CUSTOM_EN_PROFILE,
}

_SOURCE_DEFAULTS: dict[RegulationSourceType, ExtractionProfile] = {
    RegulationSourceType.LAW: LEGAL_EN_PROFILE,
    RegulationSourceType.REGULATION: LEGAL_EN_PROFILE,
    RegulationSourceType.INDUSTRY_STANDARD: STANDARD_EN_PROFILE,
    RegulationSourceType.PLATFORM_POLICY: PLATFORM_EN_PROFILE,
    RegulationSourceType.INTERNAL_POLICY: INTERNAL_EN_PROFILE,
    RegulationSourceType.CONTRACT: CONTRACT_EN_PROFILE,
    RegulationSourceType.CUSTOM_RULE: CUSTOM_EN_PROFILE,
}


def get_extraction_profile(
    *,
    source_type: RegulationSourceType,
    language: str,
    jurisdiction: str,
) -> ExtractionProfile:
    """按“精确法域 → 来源与语言 → 来源默认值”的优先级选择配置。"""
    normalized_language = language.strip().lower().split("-", maxsplit=1)[0]
    normalized_jurisdiction = jurisdiction.strip().upper() or None
    if normalized_jurisdiction in EU_JURISDICTION_ALIASES:
        normalized_jurisdiction = "EU"

    # 例如欧盟英文法规需要法域专用示例，优先于普通英文法律配置。
    exact = _EXACT_PROFILES.get((source_type, normalized_language, normalized_jurisdiction))
    if exact is not None:
        return exact

    # 没有法域特例时，根据知识来源和文档语言选择通用配置。
    if normalized_language in SUPPORTED_EXTRACTION_LANGUAGES:
        source_language = _SOURCE_LANGUAGE_PROFILES.get((source_type, normalized_language))
        if source_language is not None:
            return source_language

    # 理论上当前所有枚举都有映射；默认分支为未来新增来源类型提供
    # 可预测的英文回退，而不是让抽取流程直接崩溃。
    source_default = _SOURCE_DEFAULTS.get(source_type)
    if source_default is not None:
        return source_default

    return CUSTOM_EN_PROFILE
