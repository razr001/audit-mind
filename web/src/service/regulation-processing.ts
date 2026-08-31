import { requestApi } from './client'
import type {
  ApiResponse,
  ISODateTime,
  PageResult,
  PaginationParams,
  UUID,
} from './types'
import type {
  KnowledgeCategory,
  RegulationChunkStatus,
  RegulationIndexStatus,
  RegulationRuleStatus,
  RegulationSourceType,
  RegulationStatus,
} from './regulation-sources'

export type RegulationRuleType =
  | 'REQUIREMENT'
  | 'PROHIBITION'
  | 'RESTRICTION'
  | 'TIME_LIMIT'
  | 'PERMISSION'
  | 'EXCEPTION'
  | 'RESPONSIBILITY'
  | 'PENALTY'
  | 'APPLICABILITY'
  | 'RECOMMENDATION'

export interface RegulationUploadListResponse {
  id: UUID
  title: string
  sourceType: RegulationSourceType
  category: KnowledgeCategory
  originalFilename: string
  fileSize: number
  enabled: boolean
  status: RegulationStatus
  parseError: string | null
  parseStartedAt: ISODateTime | null
  parseCompletedAt: ISODateTime | null
  chunkStatus: RegulationChunkStatus
  chunkError: string | null
  chunkStartedAt: ISODateTime | null
  chunkCompletedAt: ISODateTime | null
  createdAt: ISODateTime
  updatedAt: ISODateTime
  indexStatus: RegulationIndexStatus
  indexError: string | null
  indexStartedAt: ISODateTime | null
  indexCompletedAt: ISODateTime | null
  ruleStatus: RegulationRuleStatus
  ruleError: string | null
  ruleStartedAt: ISODateTime | null
  ruleCompletedAt: ISODateTime | null
}

interface RegulationParseBlockMetadataResponse {
  imageCaption: string[]
  imageFootnote: string[]
  tableCaption: string[]
  tableFootnote: string[]
  chartCaption: string[]
  chartFootnote: string[]
  subType: string | null
  asset: { contentType: string; fileSize: number } | null
  aiVisualAnalysis: { description: string } | null
}

export interface RegulationParseBlockResponse {
  id: UUID
  blockIndex: number
  blockType: string
  content: string
  pageNumber: number | null
  bbox: number[] | null
  textLevel: number | null
  charStart: number
  charEnd: number
  blockMetadata: RegulationParseBlockMetadataResponse | null
}

export interface RegulationRuleResponse {
  id: UUID
  regulationId: UUID
  ruleIndex: number
  ruleType: RegulationRuleType
  topic: string | null
  subject: string | null
  action: string | null
  object: string | null
  condition: string | null
  timeLimit: string | null
  requirements: string[]
  restrictions: string[]
  exceptions: string[]
  consequences: string[]
  sourceBlockIds: UUID[]
  sourceFilename: string
  sourcePageStart: number | null
  sourcePageEnd: number | null
  sourceCharStart: number
  sourceCharEnd: number
  sourceText: string
  createdAt: ISODateTime
  updatedAt: ISODateTime
}

export interface RegulationRulesParams extends PaginationParams {
  ruleType?: RegulationRuleType
}

export function processRegulation(regulationId: UUID): Promise<ApiResponse<RegulationUploadListResponse>> {
  return requestApi<RegulationUploadListResponse>({ method: 'POST', url: `/regulation/process/${regulationId}` })
}

export function getRegulationPageBlocks(regulationId: UUID, pageNumber: number): Promise<ApiResponse<RegulationParseBlockResponse[]>> {
  return requestApi<RegulationParseBlockResponse[]>({ method: 'GET', url: `/regulation/blocks/${regulationId}`, params: { pageNumber } })
}

export function getRegulationRules(regulationId: UUID, params: RegulationRulesParams = {}): Promise<ApiResponse<PageResult<RegulationRuleResponse>>> {
  return requestApi<PageResult<RegulationRuleResponse>>({ method: 'GET', url: `/regulation/rules/${regulationId}`, params })
}
