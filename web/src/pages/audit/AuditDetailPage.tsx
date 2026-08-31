import { useCallback, useMemo, useState } from 'react'
import { useParams } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Progress, Spin, Tag } from 'antd'
import { AuditOutlined, ReloadOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { useCurrentUser } from '../../hooks/use-current-user'
import { getDocumentDownloadUrl, toMinioProxyUrl } from '../../service/document'
import { getAuditTaskPage, getAuditWorkflowTask, retryAuditWorkflowTask, type AuditFindingResponse } from '../../service/audit-task'
import { getAuditStageLabels, getAuditStatusLabels, isAuditRetryActive, terminalAuditStatuses } from './audit-options'
import { PdfAuditViewer } from './components/PdfAuditViewer'
import { MarkdownAuditViewer } from './components/MarkdownAuditViewer'
import { AuditFindingPanel } from './components/AuditFindingPanel'
import { AuditExecutionSteps } from './components/AuditExecutionSteps'
import './audit.css'

export function AuditDetailPage() {
  const { taskId } = useParams({ from: '/_authenticated/audit/$taskId' })
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [pageNumber, setPageNumber] = useState(1)
  const [selectedFinding, setSelectedFinding] = useState<AuditFindingResponse>()
  // 重试接口成功只代表队列已接受任务。Worker 领取前，查询接口仍会短暂返回
  // 原来的失败终态；记录其更新时间可识别并隐藏这份旧错误快照。
  const [retryPreviousUpdatedAt, setRetryPreviousUpdatedAt] = useState<string>()
  const currentUser = useCurrentUser()
  const userId = currentUser.data?.userId
  const taskQuery = useQuery({
    queryKey: ['audit-task', userId, taskId],
    queryFn: () => getAuditWorkflowTask(taskId),
    enabled: Boolean(userId),
    refetchInterval: (state) => {
      const latestTask = state.state.data?.data
      const retryWaiting = isAuditRetryActive(retryPreviousUpdatedAt, latestTask)
      return retryWaiting || (latestTask && !terminalAuditStatuses.includes(latestTask.status)) ? 2_000 : false
    },
  })
  const task = taskQuery.data?.data
  const retryActive = isAuditRetryActive(retryPreviousUpdatedAt, task)
  const isMarkdown = task?.documentSourceType === 'MARKDOWN'
  const unitLabel = isMarkdown ? t('common.section') : t('common.page')
  const auditStatusLabels = getAuditStatusLabels(t)
  const auditStageLabels = getAuditStageLabels(t)
  const downloadQuery = useQuery({ queryKey: ['document-download', userId, task?.documentId], queryFn: () => getDocumentDownloadUrl(task!.documentId), enabled: Boolean(userId && task?.documentId && !isMarkdown), staleTime: 25 * 60_000 })
  const pdfUrl = useMemo(() => {
    const url = downloadQuery.data?.data?.url
    return url ? toMinioProxyUrl(url) : undefined
  }, [downloadQuery.data?.data?.url])
  const refetchDownload = downloadQuery.refetch
  const pageQuery = useQuery({ queryKey: ['audit-task-page', userId, taskId, pageNumber], queryFn: () => getAuditTaskPage(taskId, pageNumber), enabled: Boolean(userId && task?.totalPages && pageNumber <= task.totalPages), refetchInterval: retryActive || (task?.status === 'RUNNING' && task.stage === 'AUDITING') ? 2_000 : false, retry: false })
  const page = pageQuery.data?.data
  const displayedPage = useMemo(() => retryActive && page?.status === 'FAILED' ? { ...page, status: 'PENDING' as const, error: null } : page, [page, retryActive])
  const evidences = useMemo(() => page?.findings.flatMap((finding) => finding.evidences) ?? [], [page])
  const selectedBlockIds = selectedFinding?.evidences.flatMap((item) => item.documentBlockId ? [item.documentBlockId] : []) ?? []
  // PDF.js 报告 URL 过期或网络失败时，重新向后端申请地址，不复用失效 URL。
  const refreshUrl = useCallback(() => { void refetchDownload() }, [refetchDownload])
  const retryMutation = useMutation({
    mutationFn: () => retryAuditWorkflowTask(taskId),
    onSuccess: async () => {
      setSelectedFinding(undefined)
      setRetryPreviousUpdatedAt(task?.updatedAt ?? '')
      void message.success(t('audit.retryStarted'))
      // 任务查询键包含 userId；终态页面已经停止轮询，必须准确失效该键
      // 才能重新取得 RUNNING 状态并恢复任务和页面进度轮询。
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['audit-task', userId, taskId] }),
        queryClient.invalidateQueries({ queryKey: ['audit-task-page', userId, taskId] }),
      ])
    },
    onError: () => void message.error(t('audit.retryFailedMessage')),
  })
  const changePage = (next: number) => { setSelectedFinding(undefined); setPageNumber(next) }

  return <AppLayout activeNavigation="tasks" headerStart={<HeaderBreadcrumb icon={<AuditOutlined />} section={t('audit.title')} current={task?.documentFilename ?? t('audit.taskDetails')} />}>
    <div className="audit-workbench reveal px-[2vw] py-4 max-[760px]:px-3">
      {!task ? <div className="grid min-h-64 place-items-center">{taskQuery.isError || currentUser.isError ? <Alert type="error" message={t('audit.loadFailed')} /> : <Spin />}</div> : <>
        <section className="audit-workbench-head panel mb-3 p-3!">
          <div className="audit-workbench-summary">
            <div className="min-w-0"><h1 className="m-0 truncate text-sm">{task.documentFilename}</h1><span className="text-[10px] text-[var(--muted)]">{isMarkdown && task.stage === 'AUDITING' ? t('audit.stage.AUDITING_SECTION') : auditStageLabels[task.stage]} · {t('audit.auditDate')} {task.auditAsOf}</span></div>
            <Progress className="max-w-72" percent={task.totalPages ? Math.round(task.completedPages * 100 / task.totalPages) : 0} size="small" />
            <span className="text-xs">{task.completedPages}/{task.totalPages || '-'} {unitLabel} · <b className="text-[var(--mint)]">{task.findingCount}</b> {t('common.findings')}</span>
            <Tag color={retryActive ? 'processing' : task.status === 'COMPLETED' ? 'success' : task.status === 'FAILED' ? 'error' : task.status === 'PARTIAL_FAILED' ? 'warning' : 'processing'}>{retryActive ? auditStatusLabels.RUNNING : auditStatusLabels[task.status]}</Tag>
            {!retryActive && (task.status === 'FAILED' || task.status === 'PARTIAL_FAILED') && <Button icon={<ReloadOutlined />} loading={retryMutation.isPending} onClick={() => retryMutation.mutate()}>{t('audit.retryFailed')}</Button>}
          </div>
          <AuditExecutionSteps stage={retryActive ? 'AUDITING' : task.stage} status={retryActive ? 'RUNNING' : task.status} completedPages={task.completedPages} totalPages={task.totalPages} sourceType={task.documentSourceType} />
        </section>
        {!retryActive && task.error && <Alert className="mb-3" type="error" showIcon message={task.error} />}
        <div className="audit-workbench-grid">
          <section className="panel min-w-0 p-0! overflow-hidden">
            {isMarkdown ? (page?.content !== null && page?.content !== undefined && page.contentStart !== null && page.contentStart !== undefined ? <MarkdownAuditViewer content={page.content} contentStart={page.contentStart} unitNumber={pageNumber} totalUnits={task.totalPages} evidences={evidences} selectedBlockIds={selectedBlockIds} onUnitChange={changePage} /> : <div className="grid min-h-[600px] place-items-center"><Spin /></div>) : pdfUrl ? <PdfAuditViewer key={pdfUrl} url={pdfUrl} pageNumber={pageNumber} evidences={evidences} selectedBlockIds={selectedBlockIds} onPageChange={changePage} onRefreshUrl={refreshUrl} /> : <div className="grid min-h-[600px] place-items-center">{downloadQuery.isError ? <Alert type="error" showIcon message={t('audit.pdfUrlFailed')} action={<Button onClick={() => void refetchDownload()}>{t('common.retry')}</Button>} /> : <Spin />}</div>}
          </section>
          <aside className="panel min-w-0 p-4! overflow-auto"><div className="mb-3 flex items-center justify-between"><strong>{t('audit.resultTitle', { number: pageNumber, unit: unitLabel })}</strong><span className="text-[10px] text-[var(--muted)]">{displayedPage?.findingCount ?? 0} {t('common.item')}</span></div>{pageQuery.isFetching && !displayedPage ? <div className="grid min-h-72 place-items-center"><Spin /></div> : <AuditFindingPanel page={displayedPage} unitLabel={isMarkdown ? 'section' : 'page'} selectedFindingId={selectedFinding?.id} onSelect={setSelectedFinding} />}</aside>
        </div>
      </>}
    </div>
  </AppLayout>
}
