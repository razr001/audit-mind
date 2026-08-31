import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRequestId } from './request-id'

afterEach(() => vi.unstubAllGlobals())

describe('createRequestId', () => {
  it('uses the browser UUID implementation when available', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'browser-request-id' })

    expect(createRequestId()).toBe('browser-request-id')
  })

  it('falls back to a backend-safe value on plain HTTP browsers', () => {
    vi.stubGlobal('crypto', {})

    expect(createRequestId()).toMatch(/^web-[a-z0-9]+-[a-z0-9]+$/)
  })
})
