import { describe, expect, it } from 'vitest'
import type { AuditEvidenceResponse } from '../../../service/audit-task'
import { buildMarkdownEvidenceRanges } from './MarkdownAuditViewer'

describe('buildMarkdownEvidenceRanges', () => {
  it('converts document offsets into current logical-unit offsets', () => {
    const evidence: AuditEvidenceResponse = {
      id: 'evidence-1',
      documentBlockId: 'block-1',
      pageNumber: 2,
      quote: '需要高亮的条款',
      bbox: null,
      charStart: 1240,
      charEnd: 1248,
    }

    expect(buildMarkdownEvidenceRanges([evidence], 1000, new Set(['block-1']))).toEqual([
      { start: 240, end: 248, selected: true },
    ])
  })

  it('ignores historical evidence without text offsets', () => {
    const evidence: AuditEvidenceResponse = {
      id: 'legacy',
      documentBlockId: null,
      pageNumber: 1,
      quote: '旧数据',
      bbox: null,
      charStart: null,
      charEnd: null,
    }

    expect(buildMarkdownEvidenceRanges([evidence], 0, new Set())).toEqual([])
  })
})
