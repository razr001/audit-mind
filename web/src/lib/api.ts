import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { showGlobalError } from '../components/feedback/GlobalMessage'
import { getAccessToken, setAccessToken } from './auth-token'
import { createRequestId } from './request-id'
import { createRefreshCoordinator } from './refresh-coordinator'

const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const api = axios.create({
  baseURL,
  timeout: 15_000,
  withCredentials: true,
})

const refreshClient = axios.create({ baseURL, timeout: 15_000, withCredentials: true })

// PDF 最大允许 100 MiB，慢速网络下不能沿用普通 API 的 15 秒超时。
export const UPLOAD_REQUEST_TIMEOUT_MS = 10 * 60_000

let unauthorizedRedirectScheduled = false

interface RetryableRequest extends InternalAxiosRequestConfig {
  authRetry?: boolean
}

export const refreshAccessToken = createRefreshCoordinator(
  () => refreshClient
    .post<{ data?: { accessToken?: string } }>('/auth/refresh', undefined, {
        headers: { 'X-Request-ID': createRequestId() },
      })
    .then((response) => {
      const token = response.data.data?.accessToken
      if (!token) throw new Error('Refresh response did not contain an access token')
      return token
    }),
  setAccessToken,
)

api.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (axios.isAxiosError(error) && error.code !== 'ERR_CANCELED') {
      if (error.response?.status === 401) {
        const request = error.config as RetryableRequest | undefined
        const isAuthEndpoint = request?.url?.includes('/auth/login') || request?.url?.includes('/auth/refresh')
        if (request && !request.authRetry && !isAuthEndpoint && getAccessToken()) {
          request.authRetry = true
          try {
            const token = await refreshAccessToken()
            request.headers.Authorization = `Bearer ${token}`
            return await api.request(request)
          } catch {
            // 统一进入下方退出流程；并发请求共享同一个刷新任务。
          }
        }
        expireAuthentication(error)
        return Promise.reject(error)
      }
      showGlobalError(getErrorMessage(error.response?.data, error.message))
    }
    return Promise.reject(error)
  },
)

function expireAuthentication(error: AxiosError): void {
  showGlobalError(getErrorMessage(error.response?.data, '登录状态已失效，请重新登录'))
  setAccessToken(null)
  if (window.location.pathname !== '/login' && !unauthorizedRedirectScheduled) {
    unauthorizedRedirectScheduled = true
    window.setTimeout(() => window.location.replace('/login'), 1_000)
  }
}

function getErrorMessage(data: unknown, fallback?: string): string {
  if (isRecord(data)) {
    if (typeof data.message === 'string' && data.message.trim()) return data.message
    if (typeof data.detail === 'string' && data.detail.trim()) return data.detail
  }
  return fallback?.trim() || '请求失败，请稍后重试'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
