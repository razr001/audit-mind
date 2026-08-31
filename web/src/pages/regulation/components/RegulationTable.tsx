import { Button, Empty, Popconfirm, Space, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CalendarOutlined, DeleteOutlined, EyeOutlined, FileTextOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import type {
  RegulationIndexStatus,
  RegulationPublicResponse,
  RegulationRuleStatus,
  RegulationSourceType,
} from '../../../service/regulation-sources'
import { getSourceTypeLabels } from './regulation-options'
import { isRegulationDetailReady, isRegulationProcessing } from './regulation-status'

interface RegulationTableProps {
  rows: RegulationPublicResponse[]
  loading: boolean
  error: boolean
  filtered: boolean
  page: number
  pageSize: number
  total: number
  deletingRegulationId?: string
  onReload: () => void
  onView: (regulation: RegulationPublicResponse) => void
  onDelete: (regulation: RegulationPublicResponse) => void
  onPageChange: (page: number, pageSize: number) => void
}

const statusColors = {
  UPLOADED: 'default', PARSING: 'processing', READY: 'success', FAILED: 'error', DELETING: 'warning',
} as const

const processingStatusColors = {
  PENDING: 'default', PROCESSING: 'processing', READY: 'success', FAILED: 'error',
} as const

function renderProcessingStatus(value: RegulationIndexStatus | RegulationRuleStatus, t: TFunction) {
  return <Tag className="!m-0 whitespace-nowrap" color={processingStatusColors[value]} bordered={false}>{t(`regulation.status.${value}`)}</Tag>
}

function createColumns(
  onView: (regulation: RegulationPublicResponse) => void,
  onDelete: (regulation: RegulationPublicResponse) => void,
  t: TFunction,
  language: string,
  deletingRegulationId?: string,
): ColumnsType<RegulationPublicResponse> {
  const sourceTypeLabels = getSourceTypeLabels(t)
  return [
  { title: t('regulation.table.name'), dataIndex: 'title', key: 'title', width: 330, render: (title: string, record) => <div className="flex items-center gap-3"><span className="grid h-[42px] w-9 shrink-0 place-items-center rounded-[8px_8px_8px_3px] border border-[rgba(128,241,198,.16)] bg-[rgba(128,241,198,.07)] text-base text-[var(--mint)] group-data-[theme=light]/app:border-[rgba(20,125,99,.13)] group-data-[theme=light]/app:bg-[rgba(20,125,99,.06)]"><FileTextOutlined /></span><div><strong className="block max-w-[255px] overflow-hidden text-ellipsis whitespace-nowrap text-xs font-semibold">{title}</strong><small className="mt-1 block max-w-[255px] overflow-hidden text-ellipsis whitespace-nowrap text-[9px] text-[var(--muted)]">{record.originalFilename}</small></div></div> },
  { title: t('regulation.table.type'), dataIndex: 'sourceType', key: 'sourceType', width: 120, render: (value: RegulationSourceType) => <Tag className="!m-0 !border-[var(--line)] !bg-transparent !text-[var(--muted)]">{sourceTypeLabels[value]}</Tag> },
  { title: t('regulation.table.number'), dataIndex: 'documentNumber', key: 'documentNumber', width: 160, render: (value: string | null) => value || <span className="text-[var(--muted)] opacity-65">—</span> },
  { title: t('regulation.table.authority'), dataIndex: 'authority', key: 'authority', ellipsis: true, render: (value: string | null) => value || <span className="text-[var(--muted)] opacity-65">{t('common.notProvided')}</span> },
  { title: t('regulation.table.effectiveDate'), dataIndex: 'effectiveDate', key: 'effectiveDate', width: 130, render: (value: string | null) => value ? <span className="inline-flex items-center gap-1.5 text-[var(--muted)]"><CalendarOutlined /> {value}</span> : <span className="text-[var(--muted)] opacity-65">—</span> },
  { title: t('regulation.table.fileStatus'), dataIndex: 'status', key: 'status', width: 105, render: (value: keyof typeof statusColors) => <Tag className="!m-0 whitespace-nowrap" color={statusColors[value]} bordered={false}>{t(`regulation.status.${value}`)}</Tag> },
  { title: t('regulation.table.indexStatus'), dataIndex: 'indexStatus', key: 'indexStatus', width: 105, render: (value) => renderProcessingStatus(value, t) },
  { title: t('regulation.table.rules'), dataIndex: 'ruleStatus', key: 'ruleStatus', width: 110, render: (value) => renderProcessingStatus(value, t) },
  { title: t('regulation.table.updatedAt'), dataIndex: 'updatedAt', key: 'updatedAt', width: 150, render: (value: string) => new Intl.DateTimeFormat(language, { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value)) },
  {
    title: t('regulation.table.actions'), key: 'actions', width: 150, fixed: 'right',
    render: (_, regulation) => {
      const detailReady = isRegulationDetailReady(regulation)
      const processing = isRegulationProcessing(regulation)
      return <Space size={2}>
        <Tooltip title={detailReady ? undefined : t('regulation.table.detailPending')}>
          <span><Button disabled={!detailReady} type="link" size="small" icon={<EyeOutlined />} onClick={() => onView(regulation)}>{t('common.details')}</Button></span>
        </Tooltip>
        {regulation.canManage ? <Tooltip title={processing ? t('regulation.table.deleteProcessing') : undefined}>
          <span><Popconfirm
            disabled={processing}
            title={t('regulation.table.confirmDelete')}
            description={t('regulation.table.deleteWarning')}
            okText={t('common.delete')}
            cancelText={t('common.cancel')}
            okButtonProps={{ danger: true }}
            onConfirm={() => onDelete(regulation)}
          >
            <Button
              danger
              type="link"
              size="small"
              icon={<DeleteOutlined />}
              loading={deletingRegulationId === regulation.id}
              disabled={processing || Boolean(deletingRegulationId && deletingRegulationId !== regulation.id)}
            >{t('common.delete')}</Button>
          </Popconfirm></span>
        </Tooltip> : <Tooltip title={t('regulation.table.ownerOnly')}><span><Button disabled danger type="link" size="small" icon={<DeleteOutlined />}>{t('common.delete')}</Button></span></Tooltip>}
      </Space>
    },
  },
  ]
}

export function RegulationTable({ rows, loading, error, filtered, page, pageSize, total, deletingRegulationId, onReload, onView, onDelete, onPageChange }: RegulationTableProps) {
  const { t, i18n } = useTranslation()
  return (
    <section className="regulation-table-card overflow-hidden rounded-[14px] border border-[var(--line)] bg-[linear-gradient(145deg,rgba(15,29,24,.94),rgba(9,20,17,.94))] shadow-[0_18px_45px_rgba(0,0,0,.08)] group-data-[theme=light]/app:bg-[linear-gradient(145deg,rgba(255,255,255,.98),rgba(249,251,248,.96))] group-data-[theme=light]/app:shadow-[0_12px_34px_rgba(29,61,49,.045)]">
      <Table<RegulationPublicResponse>
        rowKey="id"
        columns={createColumns(onView, onDelete, t, i18n.resolvedLanguage ?? 'en', deletingRegulationId)}
        dataSource={rows}
        loading={loading}
        scroll={{ x: 1490 }}
        locale={{ emptyText: error ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><span>{t('regulation.table.loadFailed')}</span><Button type="link" onClick={onReload}>{t('common.reload')}</Button></>} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t(filtered ? 'regulation.table.noMatch' : 'regulation.table.empty')} /> }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, showQuickJumper: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (count, range) => t('regulation.table.rangeTotal', { start: range[0], end: range[1], count }), onChange: onPageChange }}
      />
    </section>
  )
}
