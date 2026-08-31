import type { RegulationPublicResponse } from '../../../service/regulation-sources'

type RegulationPipelineState = Pick<
  RegulationPublicResponse,
  'status' | 'chunkStatus' | 'indexStatus' | 'ruleStatus'
>

export function isRegulationProcessing(regulation: RegulationPipelineState): boolean {
  if (regulation.status === 'DELETING') return false
  const statuses = [regulation.status, regulation.chunkStatus, regulation.indexStatus, regulation.ruleStatus]
  return !statuses.includes('FAILED') && !statuses.every((status) => status === 'READY')
}

export function isRegulationDetailReady(regulation: RegulationPipelineState): boolean {
  return regulation.status === 'READY'
    && regulation.chunkStatus === 'READY'
    && regulation.indexStatus === 'READY'
    && regulation.ruleStatus === 'READY'
}
