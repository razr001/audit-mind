import { ArrowLeftOutlined, BookOutlined, FileTextOutlined, LinkOutlined } from '@ant-design/icons'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { Alert, Button, Spin, Tag } from 'antd'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { AppLayout } from '../../components/layout/AppLayout'
import { useCurrentUser } from '../../hooks/use-current-user'
import { getRegulationPageBlocks, getRegulationRules, type RegulationRuleResponse } from '../../service/regulation-processing'
import { getRegulation, getRegulationSourceDownloadUrl, toRegulationSourceProxyUrl } from '../../service/regulation-sources'
import { getSourceTypeLabels } from './components/regulation-options'
import { RegulationRulePanel } from './components/RegulationRulePanel'
import { RegulationSourceViewer } from './components/RegulationSourceViewer'
import './regulation.css'

function statusColor(status: string) {
  if (status === 'READY') return 'success'
  if (status === 'FAILED') return 'error'
  if (status === 'PARSING' || status === 'PROCESSING') return 'processing'
  return 'default'
}

export function RegulationDetailPage() {
  const { regulationId } = useParams({ from: '/_authenticated/regulation/$regulationId' })
  const { t } = useTranslation()
  const navigate = useNavigate()
  const currentUser = useCurrentUser()
  const userId = currentUser.data?.userId
  const [rulePage, setRulePage] = useState(1)
  const [rulePageSize, setRulePageSize] = useState(20)
  const [sourceLocation, setSourceLocation] = useState({ regulationId, page: 1 })
  const [ruleSelection, setRuleSelection] = useState<{ regulationId: string; rule: RegulationRuleResponse }>()
  const sourcePage = sourceLocation.regulationId === regulationId ? sourceLocation.page : 1
  const selectedRule = ruleSelection?.regulationId === regulationId ? ruleSelection.rule : undefined
  const detailQuery = useQuery({ queryKey: ['regulation-detail', userId, regulationId], queryFn: () => getRegulation(regulationId), enabled: Boolean(userId), retry: false })
  const regulation = detailQuery.data?.data
  const sourceQuery = useQuery({ queryKey: ['regulation-source-download', userId, regulationId], queryFn: () => getRegulationSourceDownloadUrl(regulationId), enabled: Boolean(userId && regulation), staleTime: 25 * 60_000, retry: false })
  const ruleQuery = useQuery({ queryKey: ['regulation-rules', userId, regulationId, rulePage, rulePageSize], queryFn: () => getRegulationRules(regulationId, { page: rulePage, pageSize: rulePageSize }), enabled: Boolean(userId && regulation?.ruleStatus === 'READY'), placeholderData: keepPreviousData, retry: false })
  const isPdf = regulation?.contentType.toLowerCase().includes('pdf') ?? false
  const blocksQuery = useQuery({ queryKey: ['regulation-page-blocks', userId, regulationId, sourcePage], queryFn: () => getRegulationPageBlocks(regulationId, sourcePage), enabled: Boolean(userId && regulation?.status === 'READY' && isPdf), retry: false })
  const sourceUrl = useMemo(() => {
    const url = sourceQuery.data?.data?.url
    return url ? toRegulationSourceProxyUrl(url) : undefined
  }, [sourceQuery.data?.data?.url])
  const pipelineErrors = regulation ? [...new Set([regulation.parseError, regulation.chunkError, regulation.indexError, regulation.ruleError].filter((error): error is string => Boolean(error)))] : []
  const sourceTypeLabels = getSourceTypeLabels(t)
  const changeRulePage = (page: number, pageSize: number) => { setRulePage(pageSize === rulePageSize ? page : 1); setRulePageSize(pageSize) }
  const selectRule = (rule: RegulationRuleResponse) => {
    setRuleSelection({ regulationId, rule })
    // 规则来源可能跨页；先定位第一页，用户仍可在左侧继续翻页查看其余高亮块。
    if (rule.sourcePageStart !== null) setSourceLocation({ regulationId, page: rule.sourcePageStart })
  }
  const changeSourcePage = (page: number) => setSourceLocation({ regulationId, page })

  return <AppLayout activeNavigation="regulations" headerStart={<HeaderBreadcrumb icon={<BookOutlined />} section={t('regulation.knowledgeCenter')} current={t('regulation.detail')} />}>
    <main className="regulation-detail-page reveal px-[2vw] pt-4 pb-8 max-[760px]:px-3">
      <Button className="mb-3" type="text" icon={<ArrowLeftOutlined />} onClick={() => void navigate({ to: '/regulation' })}>{t('regulation.back')}</Button>
      {(detailQuery.isLoading || currentUser.isLoading) && <div className="grid min-h-80 place-items-center"><Spin /></div>}
      {(detailQuery.isError || currentUser.isError) && <Alert type="error" showIcon title={t('regulation.inaccessible')} action={<Button onClick={() => void navigate({ to: '/regulation' })}>{t('regulation.backList')}</Button>} />}
      {regulation && <>
        <section className="regulation-detail-hero">
          <div className="regulation-detail-icon"><FileTextOutlined /></div>
          <div className="min-w-0 flex-1"><div className="mb-2 flex flex-wrap items-center gap-2"><Tag color="cyan" variant="filled">{sourceTypeLabels[regulation.sourceType]}</Tag><Tag color={regulation.visibility === 'SHARED' ? 'green' : 'default'} variant="filled">{t(regulation.visibility === 'SHARED' ? 'common.shared' : 'common.private')}</Tag></div><h1>{regulation.title}</h1><p>{regulation.originalFilename}</p></div>
          <Tag color={statusColor(regulation.status)}>{t(`regulation.status.${regulation.status}`)}</Tag>
        </section>
        <section className="regulation-detail-meta">
          <div><span>{t('regulation.details.authority')}</span><strong>{regulation.authority || t('common.notProvided')}</strong></div><div><span>{t('regulation.details.jurisdiction')}</span><strong>{regulation.jurisdiction}</strong></div><div><span>{t('regulation.details.effectiveDate')}</span><strong>{regulation.effectiveDate || t('common.notSet')}</strong></div><div><span>{t('regulation.details.version')}</span><strong>{regulation.version || t('common.notProvided')}</strong></div><div><span>{t('regulation.details.ruleCount')}</span><strong>{ruleQuery.data?.data?.total ?? '—'}</strong></div>
          {regulation.sourceUrl && <a href={regulation.sourceUrl} target="_blank" rel="noreferrer"><LinkOutlined /> {t('regulation.details.source')}</a>}
        </section>
        {pipelineErrors.map((error) => <Alert className="mb-3" key={error} type="error" showIcon title={error} />)}
        <div className="regulation-detail-workbench">
          <RegulationSourceViewer url={sourceUrl} contentType={regulation.contentType} filename={regulation.originalFilename} loading={sourceQuery.isLoading} error={sourceQuery.isError} pageNumber={sourcePage} pageCount={regulation.pageCount} blocks={blocksQuery.data?.data ?? []} selectedBlockIds={selectedRule?.sourceBlockIds ?? []} blocksLoading={blocksQuery.isFetching} onPageChange={changeSourcePage} onRefresh={() => void sourceQuery.refetch()} />
          <RegulationRulePanel data={ruleQuery.data?.data ?? undefined} loading={ruleQuery.isLoading} fetching={ruleQuery.isFetching} error={ruleQuery.isError} page={rulePage} pageSize={rulePageSize} selectedRuleId={selectedRule?.id} onRuleSelect={selectRule} onPageChange={changeRulePage} />
        </div>
      </>}
    </main>
  </AppLayout>
}
