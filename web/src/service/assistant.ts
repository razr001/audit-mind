import { apiUrl, getAccessToken, requestApi, type ApiErrorResponse } from './client'
import { setAccessToken } from '../lib/auth-token'
import { refreshAccessToken } from '../lib/api'
import { createRequestId } from '../lib/request-id'
import type { ApiResponse, ISODateTime, PageResult, PaginationParams, UUID } from './types'
import type { RegulationAnswerPhase, RegulationAnswerSource } from './regulation-queries'
import i18n from '../i18n'

type AssistantMessageRole = 'USER' | 'ASSISTANT'
export type AssistantMessageStatus = 'GENERATING' | 'COMPLETED' | 'FAILED' | 'CANCELED'

export interface AssistantConversation {
  id: UUID
  title: string
  lastMessageAt: ISODateTime | null
  createdAt: ISODateTime
  updatedAt: ISODateTime
}

export interface AssistantMessage {
  id: UUID
  conversationId: UUID
  role: AssistantMessageRole
  content: string
  status: AssistantMessageStatus
  answered: boolean | null
  sources: RegulationAnswerSource[]
  createdAt: ISODateTime
}

export type AssistantStreamEvent =
  | { type: 'message-start'; data: { conversationId: UUID; userMessageId: UUID; assistantMessageId: UUID; title: string } }
  | { type: 'phase'; data: { phase: RegulationAnswerPhase } }
  | { type: 'text-delta'; data: { textDelta: string } }
  | { type: 'sources'; data: { sources: RegulationAnswerSource[] } }
  | { type: 'verified'; data: { answered: boolean } }
  | { type: 'error'; data: { code: number; message: string } }
  | { type: 'done'; data: Record<string, never> }

export function getAssistantConversations(params: PaginationParams = {}): Promise<ApiResponse<PageResult<AssistantConversation>>> {
  return requestApi<PageResult<AssistantConversation>>({ method: 'GET', url: '/assistant/conversations', params })
}

export function renameAssistantConversation(conversationId: UUID, title: string): Promise<ApiResponse<AssistantConversation>> {
  return requestApi<AssistantConversation>({ method: 'PATCH', url: `/assistant/conversations/${conversationId}`, data: { title } })
}

export function deleteAssistantConversation(conversationId: UUID): Promise<ApiResponse<null>> {
  return requestApi<null>({ method: 'DELETE', url: `/assistant/conversations/${conversationId}` })
}

export function getAssistantMessages(conversationId: UUID, params: PaginationParams = {}): Promise<ApiResponse<PageResult<AssistantMessage>>> {
  return requestApi<PageResult<AssistantMessage>>({ method: 'GET', url: `/assistant/conversations/${conversationId}/messages`, params })
}

export async function* streamAssistantMessage(
  conversationId: UUID | undefined,
  question: string,
  signal?: AbortSignal,
): AsyncGenerator<AssistantStreamEvent> {
  const token = getAccessToken()
  const path = conversationId
    ? `/assistant/conversations/${conversationId}/messages/stream`
    : '/assistant/conversations/stream'
  const request = (accessToken: string | null) => fetch(apiUrl(path), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-Request-ID': createRequestId(),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({ question }),
    signal,
    credentials: 'include',
  })
  let response = await request(token)

  if (response.status === 401 && token) {
    try {
      response = await request(await refreshAccessToken())
    } catch {
      setAccessToken(null)
      window.setTimeout(() => window.location.replace('/login'), 1_000)
    }
  }

  if (!response.ok) {
    const error = await response.json().catch(() => null) as ApiErrorResponse | null
    const message = error?.message || i18n.t('assistant.requestFailed', { status: response.status })
    if (response.status === 401) {
      setAccessToken(null)
      window.setTimeout(() => window.location.replace('/login'), 1_000)
    }
    throw new AssistantStreamError(response.status, message)
  }
  if (!response.body) throw new AssistantStreamError(response.status, i18n.t('assistant.missingStream'))

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    while (true) {
      // oxlint-disable-next-line no-await-in-loop -- SSE chunks must be consumed in order.
      const { value, done } = await reader.read()
      buffer += value ?? ''
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const event = parseFrame(frame)
        if (event) yield event
      }
      if (done) break
    }
  } finally {
    reader.releaseLock()
  }
}

function parseFrame(frame: string): AssistantStreamEvent | null {
  if (!frame || frame.startsWith(':')) return null
  let eventType = ''
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!eventType || !dataLines.length) return null
  return { type: eventType, data: JSON.parse(dataLines.join('\n')) } as AssistantStreamEvent
}

class AssistantStreamError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'AssistantStreamError'
  }
}
