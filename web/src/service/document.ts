import { requestApi } from './client'
import type {
  ApiResponse,
  UUID,
} from './types'

export type DocumentSourceType = 'PDF' | 'MARKDOWN'

export interface DocumentDownloadResponse {
  url: string
  expiresIn: number
}

export function getDocumentDownloadUrl(documentId: UUID): Promise<ApiResponse<DocumentDownloadResponse>> {
  return requestApi<DocumentDownloadResponse>({ method: 'GET', url: `/document/get/download-url/${documentId}` })
}

/** 保留签名参数，把 MinIO 地址改写为 Vite/Nginx 提供的同源代理地址。 */
export function toMinioProxyUrl(presignedUrl: string): string {
  const source = new URL(presignedUrl)
  return `/minio${source.pathname}${source.search}`
}
