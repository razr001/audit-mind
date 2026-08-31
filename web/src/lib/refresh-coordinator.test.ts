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
})
