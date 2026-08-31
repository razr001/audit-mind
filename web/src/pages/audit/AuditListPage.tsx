import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Button, Empty, Pagination, Progress, Select, Spin, Tag } from 'antd'
import { AuditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { useCurrentUser } from '../../hooks/use-current-user'
import { getAuditWorkflowTasks, type AuditStatus } from '../../service/audit-task'
import { getAuditStageLabels, getAuditStatusLabels, terminalAuditStatuses } from './audit-options'
import './audit.css'

export function AuditListPage() {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [status, setStatus] = useState<AuditStatus>()
  const currentUser = useCurrentUser()
  const userId = currentUser.data?.userId
  const query = useQuery({
    queryKey: ['audit-tasks', userId, { page, pageSize, status }],
    queryFn: () => getAuditWorkflowTasks({ page, pageSize, status }),
    enabled: Boolean(userId),
    placeholderData: (previous) => previous,
    refetchInterval: (state) => state.state.data?.data?.items.some((item) => !terminalAuditStatuses.includes(item.status)) ? 2_000 : false,
  })
  const result = query.data?.data
  const auditStatusLabels = getAuditStatusLabels(t)
  const auditStageLabels = getAuditStageLabels(t)

  return (
    <AppLayout activeNavigation="tasks" headerStart={<HeaderBreadcrumb icon={<AuditOutlined />} section={t('audit.center')} current={t('audit.title')} />}>
      <div className="audit-page reveal mx-auto max-w-[1580px] px-[3.2vw] pt-3.5 pb-14 max-[760px]:px-4.5">
        <section className="mb-5 flex items-end justify-between gap-4 max-[760px]:items-stretch max-[760px]:flex-col">
          <div><p className="mb-1! text-[7px] tracking-[1.5px] text-(--mint)">DOCUMENT AUDIT / 01</p><h1 className="m-0 text-[24px] font-semibold">{t('audit.title')}</h1><p className="mt-1 mb-0 text-[11px] text-[var(--muted)]">{t('audit.subtitle')}</p></div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate({ to: '/audit/new' })}>{t('audit.newTask')}</Button>
        </section>
        <div className="audit-toolbar panel mb-3 flex items-center justify-between gap-3 p-3!">
          <Select allowClear className="w-36" placeholder={t('audit.allStatuses')} value={status} options={Object.entries(auditStatusLabels).map(([value, label]) => ({ value, label }))} onChange={(value) => { setStatus(value); setPage(1) }} />
          <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>{t('common.refresh')}</Button>
        </div>
        <section className="audit-list panel p-0! overflow-hidden">
          {query.isLoading || currentUser.isLoading ? <div className="grid min-h-64 place-items-center"><Spin /></div> : query.isError || currentUser.isError ? <Empty description={t('audit.loadFailed')}><Button onClick={() => void query.refetch()}>{t('common.retry')}</Button></Empty> : !result?.items.length ? <Empty className="py-16" description={t('audit.empty')} /> : result.items.map((task) => {
            const percent = task.totalPages ? Math.round(task.completedPages * 100 / task.totalPages) : 0
            const unitLabel = task.documentSourceType === 'MARKDOWN' ? t('common.section') : t('common.page')
            return <button key={task.id} className="audit-row" onClick={() => navigate({ to: '/audit/$taskId', params: { taskId: task.id } })}>
              <div className="min-w-0"><strong className="block truncate text-sm">{task.documentFilename}</strong><span className="mt-1 block text-[10px] text-[var(--muted)]">{task.documentSourceType === 'MARKDOWN' && task.stage === 'AUDITING' ? t('audit.stage.AUDITING_SECTION') : auditStageLabels[task.stage]} · {new Date(task.createdAt).toLocaleString(i18n.resolvedLanguage)}</span>{task.error && <span className="mt-1 block truncate text-[10px] text-[var(--coral)]">{task.error}</span>}</div>
              <div className="w-48 max-[760px]:w-full"><Progress percent={percent} size="small" status={task.status === 'FAILED' ? 'exception' : undefined} /><span className="text-[10px] text-[var(--muted)]">{task.completedPages}/{task.totalPages || '-'} {unitLabel}</span></div>
              <span className="text-xs"><b className="text-[var(--mint)]">{task.findingCount}</b> {t('common.findings')}</span>
              <Tag color={task.status === 'COMPLETED' ? 'success' : task.status === 'FAILED' ? 'error' : task.status === 'PARTIAL_FAILED' ? 'warning' : 'processing'}>{auditStatusLabels[task.status]}</Tag>
            </button>
          })}
        </section>
        {Boolean(result?.total) && <div className="mt-4 flex justify-end">
          <Pagination
            current={page}
            pageSize={pageSize}
            total={result?.total ?? 0}
            pageSizeOptions={[10, 20, 50, 100]}
            responsive
            showQuickJumper
            showSizeChanger
            showTotal={(total, range) => t('audit.paginationTotal', { start: range[0], end: range[1], count: total })}
            onChange={(nextPage, nextPageSize) => {
              setPage(nextPageSize !== pageSize ? 1 : nextPage)
              setPageSize(nextPageSize)
            }}
          />
        </div>}
      </div>
    </AppLayout>
  )
}
