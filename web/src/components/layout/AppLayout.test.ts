import { describe, expect, it } from 'vitest'
import { getActiveNavigation } from './AppLayout'

describe('getActiveNavigation', () => {
  it.each([
    ['/audit', 'tasks'],
    ['/audit/new', 'tasks'],
    ['/regulation', 'regulations'],
    ['/regulation/source-id', 'regulations'],
    ['/assistant', 'assistant'],
    ['/users', 'users'],
  ] as const)('maps %s to %s', (pathname, navigation) => {
    expect(getActiveNavigation(pathname)).toBe(navigation)
  })
})
