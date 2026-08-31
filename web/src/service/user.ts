import { requestApi } from './client'
import type { ApiResponse, ISODateTime, UUID } from './types'

export interface ManagedUser {
  id: UUID
  username: string
  createdAt: ISODateTime
  updatedAt: ISODateTime
}

export interface CreateUserRequest {
  username: string
  password: string
}

export function listUsers(): Promise<ApiResponse<ManagedUser[]>> {
  return requestApi<ManagedUser[]>({ method: 'GET', url: '/users' })
}

export function createUser(request: CreateUserRequest): Promise<ApiResponse<ManagedUser>> {
  return requestApi<ManagedUser>({ method: 'POST', url: '/users', data: request })
}

export function deleteUser(userId: UUID): Promise<ApiResponse<null>> {
  return requestApi<null>({ method: 'DELETE', url: `/users/${userId}` })
}
