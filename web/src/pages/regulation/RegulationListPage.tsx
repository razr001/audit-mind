import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { App, Button } from 'antd'
import { HomeOutlined, PlusOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { useCurrentUser } from '../../hooks/use-current-user'
import { deleteRegulation, getRegulationList, type RegulationSourceType } from '../../service/regulation-sources'
import { CreateRegulationModal } from './components/CreateRegulationModal'
import { RegulationTable } from './components/RegulationTable'
import { RegulationToolbar } from './components/RegulationToolbar'
import './regulation.css'

export function RegulationListPage() {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [sourceType, setSourceType] = useState<RegulationSourceType>()
  const [keyword, setKeyword] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [manuallyRefreshing, setManuallyRefreshing] = useState(false)
  const deferredKeyword = useDeferredValue(keyword.trim().toLocaleLowerCase())
  const currentUser = useCurrentUser()
  const userId = currentUser.data?.userId

  const listQuery = useQuery({
    queryKey: ['regulations', userId, { page, pageSize, sourceType }],
    queryFn: () => getRegulationList({ page, pageSize, sourceType }),
    enabled: Boolean(userId),
    placeholderData: (previous) => previous,
    // 只有流水线仍在推进时才轮询。失败和全部完成都是终态，继续每五秒
    // 查询只会给 API 与 PostgreSQL 制造无效负载。
    refetchInterval: (state) => {
      const items = state.state.data?.data?.items ?? []
      const hasProcessingItem = items.some((item) => {
        // DELETING 可能表示上次删除在 ES 阶段中断，需要用户主动重试；
        // 不把它当作流水线处理中状态，避免页面永久轮询。
        if (item.status === 'DELETING') return false
        const statuses = [item.status, item.chunkStatus, item.indexStatus, item.ruleStatus]
        return !statuses.includes('FAILED') && !statuses.every((status) => status === 'READY')
      })
      return hasProcessingItem ? 5_000 : false
    },
  })

  const pageResult = listQuery.data?.data
  const rows = useMemo(() => {
    const items = pageResult?.items ?? []
    if (!deferredKeyword) return items
    return items.filter((item) => [item.title, item.documentNumber, item.authority]
      .some((value) => value?.toLocaleLowerCase().includes(deferredKeyword)))
  }, [deferredKeyword, pageResult?.items])
  const deleteMutation = useMutation({
    mutationFn: deleteRegulation,
    onSuccess: async () => {
      void message.success(t('regulation.deleted'))
      if ((pageResult?.items.length ?? 0) === 1 && page > 1) setPage(page - 1)
      await queryClient.invalidateQueries({ queryKey: ['regulations'] })
    },
  })

  const handleCreated = async () => {
    setPage(1)
    await queryClient.invalidateQueries({ queryKey: ['regulations'] })
  }

  const handleManualRefresh = async () => {
    setManuallyRefreshing(true)
    try {
      await listQuery.refetch()
    } finally {
      setManuallyRefreshing(false)
    }
  }

  return (
    <AppLayout
      activeNavigation="regulations"
      headerStart={<HeaderBreadcrumb icon={<HomeOutlined />} section={t('regulation.knowledgeCenter')} current={t('regulation.title')} />}
    >
      <div className="reveal mx-auto max-w-[1580px] px-[3.2vw] pt-3.5 pb-14 max-[760px]:px-4.5 max-[760px]:pt-3 max-[760px]:pb-10">
        <section className="mb-3 flex items-center justify-between gap-5 max-[760px]:flex-col max-[760px]:items-stretch">
          <div><p className="mb-1! text-[7px] tracking-[1.5px] text-(--mint)">KNOWLEDGE GOVERNANCE / 04</p><h1 className="m-0 font-['Manrope'] text-[clamp(20px,1.7vw,24px)] leading-[1.15] font-semibold tracking-[-.55px]">{t('regulation.title')}</h1><p className="mt-[3px] mb-0 text-[10px] text-[var(--muted)]">{t('regulation.subtitle')}</p></div>
          <Button className="h-9! px-3.5! text-xs! max-[760px]:w-full!" type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>{t('regulation.add')}</Button>
        </section>

        <RegulationToolbar
          keyword={keyword}
          sourceType={sourceType}
          total={pageResult?.total ?? 0}
          refreshing={manuallyRefreshing}
          onKeywordChange={setKeyword}
          onSourceTypeChange={(value) => { setSourceType(value); setPage(1) }}
          onRefresh={() => void handleManualRefresh()}
        />

        <RegulationTable
          rows={rows}
          loading={listQuery.isLoading}
          error={listQuery.isError}
          filtered={Boolean(deferredKeyword)}
          page={page}
          pageSize={pageSize}
          total={pageResult?.total ?? 0}
          deletingRegulationId={deleteMutation.isPending ? deleteMutation.variables : undefined}
          onReload={() => void listQuery.refetch()}
          onView={(regulation) => void navigate({ to: '/regulation/$regulationId', params: { regulationId: regulation.id } })}
          onDelete={(regulation) => deleteMutation.mutate(regulation.id)}
          onPageChange={(nextPage, nextPageSize) => {
            setPage(nextPageSize !== pageSize ? 1 : nextPage)
            setPageSize(nextPageSize)
          }}
        />
      </div>

      <CreateRegulationModal open={modalOpen} onClose={() => setModalOpen(false)} onCreated={handleCreated} />
    </AppLayout>
  )
}
