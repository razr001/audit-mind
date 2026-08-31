import { Alert, Collapse, Empty, Spin, Tag } from 'antd'
import type { AuditFindingResponse, AuditTaskPageResponse } from '../../../service/audit-task'
import { useTranslation } from 'react-i18next'

interface AuditFindingPanelProps {
  page?: AuditTaskPageResponse | null
  unitLabel?: 'page' | 'section'
  selectedFindingId?: string
  onSelect: (finding: AuditFindingResponse) => void
}

function firstEvidencePosition(finding: AuditFindingResponse): [number, number, number] {
  let first: [number, number, number] = [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER]
  for (const evidence of finding.evidences) {
    const bbox = evidence.bbox
    if (!bbox || bbox.length < 4 || !bbox.every(Number.isFinite)) continue
    const position: [number, number, number] = [evidence.pageNumber, bbox[1], bbox[0]]
    if (position[0] < first[0] || (position[0] === first[0] && (position[1] < first[1] || (position[1] === first[1] && position[2] < first[2])))) first = position
  }
  return first
}

/** 后端升级前的历史响应也按 PDF 从上到下、从左到右展示。 */
export function sortFindingsByPdfPosition(findings: AuditFindingResponse[]): AuditFindingResponse[] {
  return findings
    .map((finding, originalIndex) => ({ finding, originalIndex, position: firstEvidencePosition(finding) }))
    // map 已创建新数组，这里的原地排序不会修改接口响应或 React Query 缓存。
    // oxlint-disable-next-line unicorn/no-array-sort
    .sort((left, right) => {
      for (let index = 0; index < left.position.length; index += 1) {
        const difference = left.position[index] - right.position[index]
        if (difference !== 0) return difference
      }
      return left.originalIndex - right.originalIndex
    })
    .map(({ finding }) => finding)
}

export function AuditFindingPanel({ page, unitLabel = 'page', selectedFindingId, onSelect }: AuditFindingPanelProps) {
  const { t } = useTranslation()
  const section = unitLabel === 'section'
  if (!page) return <AuditPageLoading message={t(section ? 'audit.finding.waitingSection' : 'audit.finding.waitingPage')} />
  if (page.status === 'FAILED') return <Alert type="error" showIcon message={t(section ? 'audit.finding.failedSection' : 'audit.finding.failedPage')} description={page.error ?? t('audit.finding.retryable')} />
  if (page.status !== 'COMPLETED') return <AuditPageLoading message={t(page.status === 'RUNNING' ? (section ? 'audit.finding.auditingSection' : 'audit.finding.auditingPage') : (section ? 'audit.finding.queuedSection' : 'audit.finding.queuedPage'))} />
  if (!page.findings.length) return <div className="grid min-h-72 place-items-center"><Empty description={t(section ? 'audit.finding.emptySection' : 'audit.finding.emptyPage')} /></div>
  const orderedFindings = sortFindingsByPdfPosition(page.findings)
  return <div className="audit-findings">
    {orderedFindings.map((finding) => <article key={finding.id} className={`audit-finding ${selectedFindingId === finding.id ? 'is-selected' : ''}`}>
      <button className="audit-finding-select" onClick={() => onSelect(finding)}>
        <div className="flex items-start justify-between gap-2"><strong>{finding.title}</strong><Tag color={finding.level === 'CRITICAL' || finding.level === 'HIGH' ? 'error' : finding.level === 'MEDIUM' ? 'warning' : 'processing'}>{finding.level}</Tag></div>
        <p>{finding.description}</p>
        {finding.recommendation && <div className="audit-recommendation"><b>{t('audit.finding.recommendation')}</b><span>{finding.recommendation}</span></div>}
      </button>
      <Collapse ghost size="small" items={finding.ruleReferences.map((reference) => ({ key: reference.id, label: `${reference.sourceFilename}${reference.sourcePageStart ? ` · ${t('audit.finding.sourcePage', { page: reference.sourcePageStart })}` : ''}`, children: <div><p className="m-0 text-xs">{reference.ruleSummary}</p><blockquote>{reference.sourceText}</blockquote></div> }))} />
    </article>)}
  </div>
}

function AuditPageLoading({ message }: { message: string }) {
  const { t } = useTranslation()
  return <output className="audit-page-loading" aria-live="polite">
    <span className="audit-page-loading-halo"><Spin size="large" /></span>
    <strong>{message}</strong>
    <span>{t('audit.finding.autoRefresh')}</span>
  </output>
}
