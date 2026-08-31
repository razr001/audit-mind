import { requestApi } from './client'
import { UPLOAD_REQUEST_TIMEOUT_MS } from '../lib/api'
import type {
  ApiResponse,
  ISODateTime,
  PageResult,
  PaginationParams,
  UUID,
} from './types'
import type { DocumentSourceType } from './document'

export type AuditStatus = 'CREATED' | 'RUNNING' | 'COMPLETED' | 'PARTIAL_FAILED' | 'FAILED'
export type AuditStage = 'UPLOADING' | 'PARSING' | 'INDEXING' | 'AUDITING' | 'COMPLETED'
type AuditPageStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
export interface AuditTaskProgressResponse {
  id: UUID
  documentId: UUID
  status: AuditStatus
  createdAt: ISODateTime
  updatedAt: ISODateTime
  error: string | null
  startedAt: ISODateTime | null
  completedAt: ISODateTime | null
  stage: AuditStage
  totalPages: number
  completedPages: number
  findingCount: number
  ruleScope: AuditRuleScope
  auditAsOf: string
  documentFilename: string
  documentSourceType: DocumentSourceType
}

export interface AuditTaskListParams extends PaginationParams {
  status?: AuditStatus
}

export interface AuditRuleScope {
  regulationIds?: UUID[]
  categories?: ('PUBLIC_KNOWLEDGE' | 'COMPANY_RULE')[]
  jurisdictions?: string[]
  ruleTypes?: string[]
}

export interface AuditEvidenceResponse {
  id: UUID
  documentBlockId: UUID | null
  pageNumber: number
  quote: string
  bbox: number[] | null
  charStart: number | null
  charEnd: number | null
}

interface FindingRuleReferenceResponse {
  id: UUID
  regulationRuleId: UUID
  regulationId: UUID
  ruleType: string
  topic: string | null
  ruleSummary: string
  sourceFilename: string
  sourceContentHash: string
  sourcePageStart: number | null
  sourcePageEnd: number | null
  sourceText: string
}

export interface AuditFindingResponse {
  id: UUID
  pageNumber: number | null
  level: string
  title: string
  description: string
  recommendation: string | null
  evidences: AuditEvidenceResponse[]
  ruleReferences: FindingRuleReferenceResponse[]
}

export interface AuditTaskPageResponse {
  id: UUID
  taskId: UUID
  pageNumber: number
  status: AuditPageStatus
  attemptCount: number
  findingCount: number
  error: string | null
  startedAt: ISODateTime | null
  completedAt: ISODateTime | null
  content: string | null
  contentStart: number | null
  findings: AuditFindingResponse[]
}

export interface CreateAuditWorkflowRequest {
  file: File
  ruleScope?: AuditRuleScope
}

export interface CreateMarkdownAuditWorkflowRequest {
  title: string
  content: string
  ruleScope?: AuditRuleScope
}

export function getAuditWorkflowTasks(params: AuditTaskListParams = {}): Promise<ApiResponse<PageResult<AuditTaskProgressResponse>>> {
  return requestApi<PageResult<AuditTaskProgressResponse>>({ method: 'GET', url: '/audit/tasks', params })
}

export function getAuditWorkflowTask(taskId: UUID): Promise<ApiResponse<AuditTaskProgressResponse>> {
  return requestApi<AuditTaskProgressResponse>({ method: 'GET', url: `/audit/tasks/${taskId}` })
}

export function createAuditWorkflowTask(request: CreateAuditWorkflowRequest): Promise<ApiResponse<AuditTaskProgressResponse>> {
  const data = new FormData()
  data.append('file', request.file)
  if (request.ruleScope) data.append('ruleScope', JSON.stringify(request.ruleScope))
  return requestApi<AuditTaskProgressResponse>({ method: 'POST', url: '/audit/tasks', data, timeout: UPLOAD_REQUEST_TIMEOUT_MS })
}

export function createMarkdownAuditWorkflowTask(request: CreateMarkdownAuditWorkflowRequest): Promise<ApiResponse<AuditTaskProgressResponse>> {
  const data = new FormData()
  data.append('title', request.title)
  data.append('content', request.content)
  if (request.ruleScope) data.append('ruleScope', JSON.stringify(request.ruleScope))
  return requestApi<AuditTaskProgressResponse>({ method: 'POST', url: '/audit/tasks/markdown', data })
}

export function getAuditTaskPage(taskId: UUID, pageNumber: number): Promise<ApiResponse<AuditTaskPageResponse>> {
  return requestApi<AuditTaskPageResponse>({ method: 'GET', url: `/audit/tasks/${taskId}/pages/${pageNumber}` })
}

export function retryAuditWorkflowTask(taskId: UUID): Promise<ApiResponse<AuditTaskProgressResponse>> {
  return requestApi<AuditTaskProgressResponse>({ method: 'POST', url: `/audit/tasks/${taskId}/retry` })
}
