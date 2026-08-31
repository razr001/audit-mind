import { useQuery } from '@tanstack/react-query'
import { getCurrentUser } from '../service/auth'

/** 为所有用户隔离的查询提供稳定 userId，防止切换账号后复用旧缓存。 */
export function useCurrentUser() {
  return useQuery({
    queryKey: ['current-user'],
    queryFn: getCurrentUser,
    select: (response) => response.data,
  })
}
