import { describe, expect, it } from 'vitest'
import { isAuditRetryActive } from './audit-options'

describe('isAuditRetryActive', () => {
  const previousUpdatedAt = '2026-08-31T10:00:00Z'

  it('does not enter a transition before the user requests a retry', () => {
    expect(isAuditRetryActive(undefined, {
      status: 'PARTIAL_FAILED',
      updatedAt: previousUpdatedAt,
    })).toBe(false)
  })

  it('keeps polling while the API still returns the old failed snapshot', () => {
    expect(isAuditRetryActive(previousUpdatedAt, {
      status: 'PARTIAL_FAILED',
      updatedAt: previousUpdatedAt,
    })).toBe(true)
  })

  it('remains active after the worker changes the task to running', () => {
    expect(isAuditRetryActive(previousUpdatedAt, {
      status: 'RUNNING',
      updatedAt: '2026-08-31T10:00:01Z',
    })).toBe(true)
  })

  it('ends when the worker writes a new terminal result', () => {
    expect(isAuditRetryActive(previousUpdatedAt, {
      status: 'COMPLETED',
      updatedAt: '2026-08-31T10:00:02Z',
    })).toBe(false)
  })
})
