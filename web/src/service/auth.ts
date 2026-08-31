import { requestApi } from './client'
import type { ApiResponse, UUID } from './types'

export interface CurrentUser {
  userId: UUID
  username: string | null
}

export interface TokenResponse {
  accessToken: string
  tokenType: string
  expiresIn: number
}

export interface LoginRequest {
  username: string
  password: string
}

export function login(request: LoginRequest): Promise<ApiResponse<TokenResponse>> {
  return requestApi<TokenResponse>({ method: 'POST', url: '/auth/login', data: request })
}

export function logout(): Promise<ApiResponse<null>> {
  return requestApi<null>({ method: 'POST', url: '/auth/logout' })
}

export function createDevelopmentToken(): Promise<ApiResponse<string>> {
  return requestApi<string>({ method: 'POST', url: '/auth/create-token' })
}

export function getCurrentUser(): Promise<ApiResponse<CurrentUser>> {
  return requestApi<CurrentUser>({ method: 'GET', url: '/auth/me' })
}
