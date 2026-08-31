import type { AuditStage, AuditStatus } from '../../service/audit-task'
import type { TFunction } from 'i18next'

export function getAuditStatusLabels(t: TFunction): Record<AuditStatus, string> {
  return { CREATED: t('audit.status.CREATED'), RUNNING: t('audit.status.RUNNING'), COMPLETED: t('audit.status.COMPLETED'), PARTIAL_FAILED: t('audit.status.PARTIAL_FAILED'), FAILED: t('audit.status.FAILED') }
}

export function getAuditStageLabels(t: TFunction): Record<AuditStage, string> {
  return { UPLOADING: t('audit.stage.UPLOADING'), PARSING: t('audit.stage.PARSING'), INDEXING: t('audit.stage.INDEXING'), AUDITING: t('audit.stage.AUDITING'), COMPLETED: t('audit.stage.COMPLETED') }
}

export const terminalAuditStatuses: AuditStatus[] = ['COMPLETED', 'PARTIAL_FAILED', 'FAILED']

interface AuditRetryTaskSnapshot {
  status: AuditStatus
  updatedAt: string
}

/**
 * 判断查询结果是否仍处于“重试已入队、Worker 尚未产出新终态”的过渡期。
 * 失败终态且更新时间未变化，说明它只是重试前的旧快照，前端应继续轮询。
 */
export function isAuditRetryActive(
  previousUpdatedAt: string | undefined,
  task: AuditRetryTaskSnapshot | null | undefined,
): boolean {
  if (previousUpdatedAt === undefined) return false
  if (!task) return true
  if (!terminalAuditStatuses.includes(task.status)) return true
  return task.updatedAt === previousUpdatedAt
}
