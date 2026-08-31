import { describe, expect, it } from 'vitest'
import type { AuditFindingResponse } from '../../../service/audit-task'
import { sortFindingsByPdfPosition } from './AuditFindingPanel'

function finding(id: string, bbox: number[] | null): AuditFindingResponse {
  return {
    id,
    pageNumber: 1,
    level: 'HIGH',
    title: id,
    description: id,
    recommendation: null,
    evidences: [{ id: `${id}-evidence`, documentBlockId: id, pageNumber: 1, quote: id, bbox, charStart: null, charEnd: null }],
    ruleReferences: [],
  }
}

describe('sortFindingsByPdfPosition', () => {
  it('orders findings from the top of the PDF page to the bottom', () => {
    const bottom = finding('bottom', [97, 829, 894, 890])
    const top = finding('top', [97, 523, 895, 583])
    const sameBottom = finding('same-bottom', [97, 829, 894, 890])

    expect(sortFindingsByPdfPosition([bottom, top, sameBottom]).map((item) => item.id)).toEqual([
      'top',
      'bottom',
      'same-bottom',
    ])
  })

  it('keeps findings without a valid bbox at the end', () => {
    expect(sortFindingsByPdfPosition([finding('unknown', null), finding('known', [10, 20, 30, 40])]).map((item) => item.id)).toEqual([
      'known',
      'unknown',
    ])
  })
})
