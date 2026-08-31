import { Button, Input, Select, Tooltip } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { RegulationSourceType } from '../../../service/regulation-sources'
import { getSourceTypeLabels } from './regulation-options'

interface RegulationToolbarProps {
  keyword: string
  sourceType?: RegulationSourceType
  total: number
  refreshing: boolean
  onKeywordChange: (value: string) => void
  onSourceTypeChange: (value?: RegulationSourceType) => void
  onRefresh: () => void
}

export function RegulationToolbar({ keyword, sourceType, total, refreshing, onKeywordChange, onSourceTypeChange, onRefresh }: RegulationToolbarProps) {
  const { t } = useTranslation()
  const sourceTypeLabels = getSourceTypeLabels(t)
  return (
    <section className="mb-3.5 flex items-center gap-2.5 rounded-[14px] border border-[var(--line)] bg-[linear-gradient(145deg,rgba(15,29,24,.92),rgba(9,20,17,.92))] p-3.5 group-data-[theme=light]/app:bg-[linear-gradient(145deg,rgba(255,255,255,.98),rgba(249,251,248,.96))] group-data-[theme=light]/app:shadow-[0_12px_34px_rgba(29,61,49,.045)] max-[900px]:flex-wrap">
      <Input className="!w-[min(430px,42vw)] max-[900px]:!w-full" allowClear prefix={<SearchOutlined />} placeholder={t('regulation.searchPlaceholder')} value={keyword} onChange={(event) => onKeywordChange(event.target.value)} />
      <Select<RegulationSourceType>
        className="!w-[180px] max-[760px]:!w-auto max-[760px]:flex-1"
        allowClear
        placeholder={t('regulation.allTypes')}
        value={sourceType}
        onChange={onSourceTypeChange}
        options={Object.entries(sourceTypeLabels).map(([value, label]) => ({ value: value as RegulationSourceType, label }))}
      />
      <Tooltip title={t('regulation.refreshList')}><Button className="!w-9 !px-0" icon={<ReloadOutlined />} onClick={onRefresh} loading={refreshing} /></Tooltip>
      <span className="ml-auto pr-1.5 text-[11px] text-[var(--muted)] max-[900px]:ml-0">{t('regulation.total', { count: total })}</span>
    </section>
  )
}
