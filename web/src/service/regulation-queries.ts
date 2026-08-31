import type { UUID } from './types'

export interface RegulationAnswerSource {
  chunkId: UUID
  regulationId: UUID
  title: string
  pageNumber: number | null
  pageStart: number | null
  pageEnd: number | null
  quote: string
}

export type RegulationAnswerPhase =
  | 'guarding'
  | 'understanding'
  | 'retrieving'
  | 'reranking'
  | 'screening-context'
  | 'generating'
  | 'validating'
  | 'screening-output'
