import { describe, expect, it, vi } from 'vitest'
import { createRefreshCoordinator } from './refresh-coordinator'

describe('refresh coordinator', () => {
  it('shares one refresh request between concurrent callers', async () => {
    let resolveToken: ((token: string) => void) | undefined
    const request = vi.fn(() => new Promise<string>((resolve) => { resolveToken = resolve }))
    const store = vi.fn()
    const refresh = createRefreshCoordinator(request, store)

    const first = refresh()
    const second = refresh()
    expect(request).toHaveBeenCalledTimes(1)

    resolveToken?.('new-access-token')
    await expect(Promise.all([first, second])).resolves.toEqual([
      'new-access-token',
      'new-access-token',
    ])
    expect(store).toHaveBeenCalledOnce()

    request.mockResolvedValueOnce('second-token')
    await expect(refresh()).resolves.toBe('second-token')
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('coordinates refresh token rotation between browser tabs', async () => {
    let storedToken = 'expired-access-token'
    let lockTail = Promise.resolve()
    const runExclusive = async <T>(operation: () => Promise<T>): Promise<T> => {
      const previous = lockTail
      let releaseLock: (() => void) | undefined
      lockTail = new Promise<void>((resolve) => { releaseLock = resolve })
      await previous
      try {
        return await operation()
      } finally {
        releaseLock?.()
      }
    }
    let resolveRequest: ((token: string) => void) | undefined
    const request = vi.fn(() => new Promise<string>((resolve) => { resolveRequest = resolve }))
    const store = (token: string) => { storedToken = token }
    const options = {
      readStoredToken: () => storedToken,
      runExclusive,
    }
    // 两个 coordinator 模拟两个拥有独立 JS 上下文的标签页。
    const firstTabRefresh = createRefreshCoordinator(request, store, options)
    const secondTabRefresh = createRefreshCoordinator(request, store, options)

    const first = firstTabRefresh()
    const second = secondTabRefresh()
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1))

    resolveRequest?.('shared-new-access-token')
    await expect(Promise.all([first, second])).resolves.toEqual([
      'shared-new-access-token',
      'shared-new-access-token',
    ])
    expect(request).toHaveBeenCalledTimes(1)
    expect(storedToken).toBe('shared-new-access-token')
  })
})
