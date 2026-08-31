import { describe, expect, it } from 'vitest'
import { getAuditStepIndex } from './AuditExecutionSteps'

describe('getAuditStepIndex', () => {
  it('maps backend stages to the visible four-step workflow', () => {
    expect(getAuditStepIndex('UPLOADING')).toBe(0)
    expect(getAuditStepIndex('PARSING')).toBe(1)
    expect(getAuditStepIndex('INDEXING')).toBe(2)
    expect(getAuditStepIndex('AUDITING')).toBe(2)
    expect(getAuditStepIndex('COMPLETED')).toBe(3)
  })
})
