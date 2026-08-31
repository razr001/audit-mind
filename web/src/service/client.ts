import type { AxiosRequestConfig } from 'axios'
import { api } from '../lib/api'
import { getAccessToken } from '../lib/auth-token'
import { createRequestId } from '../lib/request-id'
import type { ApiResponse } from './types'

export { getAccessToken, setAccessToken } from '../lib/auth-token'

interface ApiValidationError {
  field: string
  message: string
  type: string
}

interface ApiErrorData {
  errors?: ApiValidationError[]
  [key: string]: unknown
}

export interface ApiErrorResponse {
  code: number
  message: string
  data: ApiErrorData | null
  /** Exception handlers are plain JSON responses, so this key stays snake_case. */
  request_id?: string | null
}

api.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  config.headers['X-Request-ID'] = createRequestId()
  return config
})

export async function requestApi<T>(config: AxiosRequestConfig): Promise<ApiResponse<T>> {
  const response = await api.request<ApiResponse<T>>(config)
  return response.data
}

export function apiUrl(path: string): string {
  const base = String(api.defaults.baseURL ?? '').replace(/\/$/, '')
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}
