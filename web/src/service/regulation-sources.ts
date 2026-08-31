import { requestApi } from './client'
import { UPLOAD_REQUEST_TIMEOUT_MS } from '../lib/api'
import type {
  ApiResponse,
  ISODate,
  ISODateTime,
  PageResult,
  PaginationParams,
  UUID,
} from './types'

export type RegulationSourceType =
  | 'LAW'
  | 'REGULATION'
  | 'INDUSTRY_STANDARD'
  | 'PLATFORM_POLICY'
  | 'INTERNAL_POLICY'
  | 'CONTRACT'
  | 'CUSTOM_RULE'

export type KnowledgeCategory = 'PUBLIC_KNOWLEDGE' | 'COMPANY_RULE'
export type KnowledgeVisibility = 'SHARED' | 'PRIVATE'
export type RegulationStatus = 'UPLOADED' | 'PARSING' | 'READY' | 'FAILED' | 'DELETING'
export type RegulationChunkStatus = 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED'
export type RegulationIndexStatus = 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED'
export type RegulationRuleStatus = 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED'

export interface RegulationUploadForm {
  title: string
  sourceType?: RegulationSourceType
  visibility?: KnowledgeVisibility
  language?: string
  documentNumber?: string | null
  authority?: string | null
  jurisdiction?: string
  effectiveDate?: ISODate | null
  expirationDate?: ISODate | null
  version?: string | null
  sourceUrl?: string | null
}

export interface RegulationUploadRequest extends RegulationUploadForm {
  file: File
}

export interface RegulationTextCreateRequest extends RegulationUploadForm {
  content: string
}

export interface RegulationUploadResponse {
  id: UUID
  title: string
  category: KnowledgeCategory
  visibility: KnowledgeVisibility
  originalFilename: string
  status: RegulationStatus
}

export interface RegulationPublicResponse {
  id: UUID
  title: string
  sourceType: RegulationSourceType
  category: KnowledgeCategory
  visibility: KnowledgeVisibility
  language: string
  documentNumber: string | null
  authority: string | null
  jurisdiction: string
  effectiveDate: ISODate | null
  expirationDate: ISODate | null
  version: string | null
  sourceUrl: string | null
  originalFilename: string
  contentType: string
  fileSize: number
  enabled: boolean
  status: RegulationStatus
  parseStartedAt: ISODateTime | null
  parseCompletedAt: ISODateTime | null
  chunkStatus: RegulationChunkStatus
  chunkStartedAt: ISODateTime | null
  chunkCompletedAt: ISODateTime | null
  createdAt: ISODateTime
  updatedAt: ISODateTime
  indexStatus: RegulationIndexStatus
  indexStartedAt: ISODateTime | null
  indexCompletedAt: ISODateTime | null
  ruleStatus: RegulationRuleStatus
  ruleStartedAt: ISODateTime | null
  ruleCompletedAt: ISODateTime | null
  canManage: boolean
}

export interface RegulationDetailResponse extends RegulationPublicResponse {
  canManage: boolean
  pageCount: number
  parseError: string | null
  chunkError: string | null
  indexError: string | null
  ruleError: string | null
}

export interface RegulationSourceDownloadResponse {
  regulationId: UUID
  url: string
  expiresIn: number
  originalFilename: string
  contentType: string
  pageCount: number
}

export interface RegulationListParams extends PaginationParams {
  category?: KnowledgeCategory
  sourceType?: RegulationSourceType
}

export function getRegulationSourceDownloadUrl(regulationId: UUID): Promise<ApiResponse<RegulationSourceDownloadResponse>> {
  return requestApi<RegulationSourceDownloadResponse>({ method: 'GET', url: `/regulation/get/download-url/${regulationId}` })
}

/** 保留 MinIO 签名参数，通过本地 Vite 或生产 Nginx 的同源代理加载法规原文。 */
export function toRegulationSourceProxyUrl(presignedUrl: string): string {
  const source = new URL(presignedUrl)
  return `/minio${source.pathname}${source.search}`
}

export function getRegulation(regulationId: UUID): Promise<ApiResponse<RegulationDetailResponse>> {
  return requestApi<RegulationDetailResponse>({ method: 'GET', url: `/regulation/get/${regulationId}` })
}

export function getRegulationList(params: RegulationListParams = {}): Promise<ApiResponse<PageResult<RegulationPublicResponse>>> {
  return requestApi<PageResult<RegulationPublicResponse>>({ method: 'GET', url: '/regulation/list', params })
}

export function uploadRegulation(request: RegulationUploadRequest): Promise<ApiResponse<RegulationUploadResponse>> {
  const { file, ...fields } = request
  const data = new FormData()
  data.append('file', file)
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== undefined && value !== null) data.append(key, String(value))
  })
  return requestApi<RegulationUploadResponse>({ method: 'POST', url: '/regulation/upload', data, timeout: UPLOAD_REQUEST_TIMEOUT_MS })
}

export function createRegulationText(request: RegulationTextCreateRequest): Promise<ApiResponse<RegulationUploadResponse>> {
  return requestApi<RegulationUploadResponse>({ method: 'POST', url: '/regulation/text', data: request })
}

export function deleteRegulation(regulationId: UUID): Promise<ApiResponse<UUID>> {
  return requestApi<UUID>({ method: 'DELETE', url: `/regulation/${regulationId}` })
}
