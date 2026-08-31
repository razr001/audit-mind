/** Only transport primitives shared by multiple service modules live here. */
export type UUID = string
export type ISODate = string
export type ISODateTime = string

export interface ApiResponse<T> {
  code: number
  message: string
  data: T | null
}

export interface PageResult<T> {
  total: number
  items: T[]
  page: number
  pageSize: number
  totalPages: number
}

export interface PaginationParams {
  page?: number
  pageSize?: number
}
