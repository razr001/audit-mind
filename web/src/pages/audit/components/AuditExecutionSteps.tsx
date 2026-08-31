import { LoadingOutlined } from '@ant-design/icons'
import { Steps } from 'antd'
import type { AuditStage, AuditStatus } from '../../../service/audit-task'
import type { DocumentSourceType } from '../../../service/document'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

interface AuditExecutionStepsProps {
  stage: AuditStage
  status: AuditStatus
  completedPages: number
  totalPages: number
  sourceType?: DocumentSourceType
}

export function getAuditStepIndex(stage: AuditStage): number {
  if (stage === 'UPLOADING') return 0
  if (stage === 'PARSING') return 1
  // 旧任务可能仍处于 INDEXING；在新流程中它属于审计前的数据准备阶段。
  if (stage === 'INDEXING' || stage === 'AUDITING') return 2
  return 3
}

function currentStepDescription({ stage, status, completedPages, totalPages, sourceType }: AuditExecutionStepsProps, t: TFunction): string {
  const markdown = sourceType === 'MARKDOWN'
  if (status === 'FAILED') return t('audit.steps.failed')
  if (status === 'PARTIAL_FAILED') return t(markdown ? 'audit.steps.partialSection' : 'audit.steps.partialPage')
  if (status === 'COMPLETED') return t(markdown ? 'audit.steps.completedSection' : 'audit.steps.completedPage')
  if (stage === 'UPLOADING') return t('audit.steps.uploading')
  if (stage === 'PARSING') return t(markdown ? 'audit.steps.parsingText' : 'audit.steps.parsingPdf')
  if (stage === 'INDEXING') return t('audit.steps.preparing')
  if (stage === 'AUDITING') return t(markdown ? 'audit.steps.auditingSection' : 'audit.steps.auditingPage', { completed: completedPages, total: totalPages || '-' })
  return t('audit.steps.finalizing')
}

export function AuditExecutionSteps(props: AuditExecutionStepsProps) {
  const { t } = useTranslation()
  const markdown = props.sourceType === 'MARKDOWN'
  const steps = [
    { key: 'UPLOADING', label: t('audit.steps.receive') },
    { key: 'PARSING', label: t(markdown ? 'audit.steps.parseText' : 'audit.steps.parsePdf') },
    { key: 'AUDITING', label: t(markdown ? 'audit.steps.auditSection' : 'audit.steps.auditPage') },
    { key: 'COMPLETED', label: t('audit.steps.result') },
  ] as const
  const currentIndex = getAuditStepIndex(props.stage)
  const failed = props.status === 'FAILED'
  const partialFailed = props.status === 'PARTIAL_FAILED'
  const completed = props.status === 'COMPLETED'
  const processing = props.status === 'CREATED' || props.status === 'RUNNING'

  return <div className="audit-execution" aria-label={t('audit.steps.aria')}>
    <Steps
      className="audit-execution-steps"
      size="small"
      current={completed ? steps.length : currentIndex}
      status={failed || partialFailed ? 'error' : 'process'}
      items={steps.map((step, index) => ({
        key: step.key,
        title: step.label,
        icon: processing && index === currentIndex ? <LoadingOutlined spin /> : undefined,
      }))}
    />
    <p className={failed || partialFailed ? 'is-warning' : ''}>{currentStepDescription(props, t)}</p>
  </div>
}
