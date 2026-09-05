type ExclusiveRunner = <T>(operation: () => Promise<T>) => Promise<T>

interface RefreshCoordinatorOptions {
  readStoredToken?: () => string | null
  runExclusive?: ExclusiveRunner
}

const REFRESH_LOCK_NAME = 'auditmind-refresh-token-rotation'

async function runWithBrowserLock<T>(operation: () => Promise<T>): Promise<T> {
  if (typeof navigator === 'undefined' || !navigator.locks) return operation()
  return navigator.locks.request(REFRESH_LOCK_NAME, operation)
}

export function createRefreshCoordinator(
  requestToken: () => Promise<string>,
  storeToken: (token: string) => void,
  options: RefreshCoordinatorOptions = {},
): () => Promise<string> {
  let pending: Promise<string> | null = null
  const runExclusive = options.runExclusive ?? runWithBrowserLock

  return () => {
    if (!pending) {
      // localStorage 与 Refresh Cookie 都在同源标签页间共享，但模块变量不是。
      // 记录进入锁之前看到的 Access Token；若另一个标签页已经完成轮换，
      // 直接复用它写入的新 Token，避免再次消费同一个一次性 Refresh Token。
      const observedToken = options.readStoredToken?.() ?? null
      pending = runExclusive(async () => {
        const currentToken = options.readStoredToken?.() ?? null
        if (currentToken && currentToken !== observedToken) return currentToken

        const token = await requestToken()
        storeToken(token)
        return token
      })
        .finally(() => {
          pending = null
        })
    }
    return pending
  }
}
