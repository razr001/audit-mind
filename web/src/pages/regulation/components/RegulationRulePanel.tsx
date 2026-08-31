import { ApartmentOutlined, FileSearchOutlined } from '@ant-design/icons'
import { Alert, Empty, Pagination, Skeleton, Tag } from 'antd'
import type { RegulationRuleResponse, RegulationRuleType } from '../../../service/regulation-processing'
import type { PageResult } from '../../../service/types'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

interface RegulationRulePanelProps {
  data?: PageResult<RegulationRuleResponse>
  loading: boolean
  fetching: boolean
  error: boolean
  page: number
  pageSize: number
  selectedRuleId?: string
  onRuleSelect: (rule: RegulationRuleResponse) => void
  onPageChange: (page: number, pageSize: number) => void
}

const ruleTypeColors: Record<RegulationRuleType, string> = {
  REQUIREMENT: 'cyan', PROHIBITION: 'red', RESTRICTION: 'orange', TIME_LIMIT: 'gold', PERMISSION: 'green', EXCEPTION: 'purple', RESPONSIBILITY: 'blue', PENALTY: 'volcano', APPLICABILITY: 'geekblue', RECOMMENDATION: 'lime',
}

function ruleHeadline(rule: RegulationRuleResponse, t: TFunction) {
  return rule.topic || rule.action || rule.subject || t('regulation.rules.fallback', { number: rule.ruleIndex + 1 })
}

function RuleList({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null
  return <div className="regulation-rule-list"><span>{label}</span><ul>{[...new Set(values)].map((value) => <li key={`${label}-${value}`}>{value}</li>)}</ul></div>
}

function RuleCard({ rule, selected, onSelect }: { rule: RegulationRuleResponse; selected: boolean; onSelect: () => void }) {
  const { t } = useTranslation()
  const pageLabel = rule.sourcePageStart === null ? t('regulation.rules.unknownPage') : rule.sourcePageStart === rule.sourcePageEnd || rule.sourcePageEnd === null ? t('regulation.rules.singlePage', { start: rule.sourcePageStart }) : t('regulation.rules.pageRange', { start: rule.sourcePageStart, end: rule.sourcePageEnd })
  return <article className={`regulation-rule-card${selected ? ' is-selected' : ''}`}>
    <button type="button" className="regulation-rule-select" aria-pressed={selected} onClick={onSelect}>
      <div className="regulation-rule-card-top"><span className="regulation-rule-number">{String(rule.ruleIndex + 1).padStart(2, '0')}</span><div className="min-w-0 flex-1"><h3>{ruleHeadline(rule, t)}</h3><span><FileSearchOutlined /> {pageLabel}</span></div><Tag color={ruleTypeColors[rule.ruleType]} variant="filled">{t(`regulation.rules.type.${rule.ruleType}`)}</Tag></div>
      <div className="regulation-rule-facts">
        {rule.subject && <p><span>{t('regulation.rules.subject')}</span>{rule.subject}</p>}{rule.action && <p><span>{t('regulation.rules.action')}</span>{rule.action}</p>}{rule.object && <p><span>{t('regulation.rules.object')}</span>{rule.object}</p>}{rule.condition && <p><span>{t('regulation.rules.condition')}</span>{rule.condition}</p>}{rule.timeLimit && <p><span>{t('regulation.rules.timeLimit')}</span>{rule.timeLimit}</p>}
      </div>
      <RuleList label={t('regulation.rules.requirements')} values={rule.requirements} /><RuleList label={t('regulation.rules.restrictions')} values={rule.restrictions} /><RuleList label={t('regulation.rules.exceptions')} values={rule.exceptions} /><RuleList label={t('regulation.rules.consequences')} values={rule.consequences} />
      <blockquote>{rule.sourceText}</blockquote>
    </button>
  </article>
}

export function RegulationRulePanel({ data, loading, fetching, error, page, pageSize, selectedRuleId, onRuleSelect, onPageChange }: RegulationRulePanelProps) {
  const { t } = useTranslation()
  return <aside className="regulation-rule-pane">
    <header className="regulation-workbench-heading"><div className="regulation-workbench-title"><span className="regulation-workbench-icon"><ApartmentOutlined /></span><div><strong>{t('regulation.rules.title')}</strong><span>{t('regulation.rules.subtitle')}</span></div></div><span className="regulation-rule-total">{t('regulation.rules.count', { count: data?.total ?? 0 })}</span></header>
    <div className={`regulation-rule-scroll${fetching && data ? ' is-fetching' : ''}`}>
      {loading && <div className="regulation-rule-skeleton"><Skeleton active paragraph={{ rows: 5 }} /><Skeleton active paragraph={{ rows: 4 }} /></div>}
      {!loading && error && <div className="regulation-pane-state"><Alert type="error" showIcon title={t('regulation.rules.loadFailed')} description={t('regulation.rules.loadFailedDescription')} /></div>}
      {!loading && !error && !data?.items.length && <div className="regulation-pane-state"><Empty description={t('regulation.rules.empty')} /></div>}
      {data?.items.map((rule) => <RuleCard key={rule.id} rule={rule} selected={selectedRuleId === rule.id} onSelect={() => onRuleSelect(rule)} />)}
    </div>
    {Boolean(data?.total) && <footer className="regulation-rule-pagination"><Pagination current={page} pageSize={pageSize} total={data?.total} pageSizeOptions={[10, 20, 50]} showSizeChanger showQuickJumper showTotal={(total) => t('regulation.rules.total', { count: total })} onChange={onPageChange} /></footer>}
  </aside>
}
