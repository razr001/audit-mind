import { describe, expect, it } from 'vitest'
import { isRegulationDetailReady, isRegulationProcessing } from './regulation-status'

const ready = {
  status: 'READY',
  chunkStatus: 'READY',
  indexStatus: 'READY',
  ruleStatus: 'READY',
} as const

describe('regulation action status', () => {
  it.each([
    { ...ready, status: 'UPLOADED' as const, chunkStatus: 'PENDING' as const },
    { ...ready, status: 'PARSING' as const },
    { ...ready, chunkStatus: 'PROCESSING' as const },
    { ...ready, indexStatus: 'PROCESSING' as const },
    { ...ready, ruleStatus: 'PROCESSING' as const },
  ])('disables deletion while any pipeline stage is processing', (regulation) => {
    expect(isRegulationProcessing(regulation)).toBe(true)
  })

  it('only enables details after every stage is ready', () => {
    expect(isRegulationDetailReady(ready)).toBe(true)
    expect(isRegulationDetailReady({ ...ready, ruleStatus: 'FAILED' })).toBe(false)
  })

  it('keeps failed terminal records deletable', () => {
    expect(isRegulationProcessing({ ...ready, ruleStatus: 'FAILED' })).toBe(false)
  })
})
