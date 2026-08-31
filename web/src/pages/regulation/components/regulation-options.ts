import type { RegulationSourceType } from '../../../service/regulation-sources'
import type { TFunction } from 'i18next'

export function getSourceTypeLabels(t: TFunction): Record<RegulationSourceType, string> {
  return {
    LAW: t('regulation.sourceType.LAW'), REGULATION: t('regulation.sourceType.REGULATION'), INDUSTRY_STANDARD: t('regulation.sourceType.INDUSTRY_STANDARD'),
    PLATFORM_POLICY: t('regulation.sourceType.PLATFORM_POLICY'), INTERNAL_POLICY: t('regulation.sourceType.INTERNAL_POLICY'), CONTRACT: t('regulation.sourceType.CONTRACT'), CUSTOM_RULE: t('regulation.sourceType.CUSTOM_RULE'),
  }
}
