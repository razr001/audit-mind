import type { ChatTransport, UIMessage, UIMessageChunk } from 'ai'
import { streamAssistantMessage, type AssistantAnswerPhase } from '../../service/assistant'
import { createRequestId } from '../../lib/request-id'
import type { RegulationAnswerSource } from '../../service/regulation-queries'
import i18n from '../../i18n'

// useChat 当前每 25ms 最多刷新一次消息。动画间隔必须大于这个值，
// 否则多个文本块会再次被合并，短回答看起来仍然像一次性出现。
const TYPING_CHUNK_CHARACTERS = 2
const TYPING_INTERVAL_MS = 32

interface AssistantMessageMetadata {
  answered?: boolean
  status?: import('../../service/assistant').AssistantMessageStatus
}

type AssistantMessageData = {
  conversation: { conversationId: string; title: string }
  phase: { phase: AssistantAnswerPhase }
  sources: { sources: RegulationAnswerSource[] }
}

export type AssistantUIMessage = UIMessage<AssistantMessageMetadata, AssistantMessageData>

export class AssistantChatTransport implements ChatTransport<AssistantUIMessage> {
  constructor(private conversationId?: string) {}

  setConversationId(conversationId?: string) {
    this.conversationId = conversationId
  }

  async sendMessages({ messages, abortSignal }: Parameters<ChatTransport<AssistantUIMessage>['sendMessages']>[0]): Promise<ReadableStream<UIMessageChunk>> {
    const question = getMessageText(messages.at(-1))
    if (!question) throw new Error(i18n.t('assistant.enterQuestion'))
    const conversationId = this.conversationId

    return new ReadableStream<UIMessageChunk>({
      start: (controller) => {
        void this.pipeEvents(conversationId, question, abortSignal, controller)
      },
      cancel: () => undefined,
    })
  }

  async reconnectToStream(): Promise<ReadableStream<UIMessageChunk> | null> {
    return null
  }

  private async pipeEvents(
    conversationId: string | undefined,
    question: string,
    signal: AbortSignal | undefined,
    controller: ReadableStreamDefaultController<UIMessageChunk>,
  ) {
    const textPartId = createRequestId()
    let started = false
    let finished = false
    try {
      for await (const event of streamAssistantMessage(conversationId, question, signal)) {
        if (event.type === 'message-start') {
          this.conversationId = event.data.conversationId
          controller.enqueue({ type: 'start', messageId: event.data.assistantMessageId })
          controller.enqueue({
            type: 'data-conversation',
            data: { conversationId: event.data.conversationId, title: event.data.title },
            transient: true,
          })
          controller.enqueue({ type: 'start-step' })
          controller.enqueue({ type: 'text-start', id: textPartId })
          started = true
        } else if (event.type === 'text-delta') {
          await enqueueTextWithTypingEffect(
            controller,
            textPartId,
            event.data.textDelta,
            signal,
          )
        } else if (event.type === 'phase') {
          controller.enqueue({ type: 'data-phase', data: event.data, transient: true })
        } else if (event.type === 'sources') {
          controller.enqueue({ type: 'data-sources', data: event.data })
        } else if (event.type === 'verified') {
          controller.enqueue({ type: 'message-metadata', messageMetadata: { answered: event.data.answered } })
        } else if (event.type === 'error') {
          controller.enqueue({ type: 'message-metadata', messageMetadata: { status: 'FAILED' } })
          controller.enqueue({ type: 'error', errorText: event.data.message })
          controller.close()
          return
        } else if (event.type === 'done') {
          if (started) {
            controller.enqueue({ type: 'text-end', id: textPartId })
            controller.enqueue({ type: 'finish-step' })
          }
          controller.enqueue({ type: 'finish', finishReason: 'stop' })
          finished = true
        }
      }
      if (!finished) throw new Error(i18n.t('assistant.connectionInterrupted'))
      controller.close()
    } catch (error) {
      if (signal?.aborted) {
        controller.enqueue({ type: 'message-metadata', messageMetadata: { status: 'CANCELED' } })
        controller.enqueue({ type: 'abort', reason: i18n.t('assistant.stoppedByUser') })
        controller.close()
        return
      }
      // 网络中断、SSE 提前结束等异常也要给本地消息一个明确的终态，
      // 否则页面会一直显示“正在生成回答…”。
      const message = error instanceof Error ? error.message : i18n.t('assistant.connectionError')
      controller.enqueue({ type: 'message-metadata', messageMetadata: { status: 'FAILED' } })
      controller.enqueue({ type: 'error', errorText: message })
      controller.close()
    }
  }
}

/**
 * 将服务端文本块进一步拆成适合逐帧绘制的小块。
 *
 * Agent 的最终回答必须先经过来源与输出安全校验，因此服务端会在校验完成后
 * 很快发出多个 text-delta。若前端立即消费，React 会把更新合并成一次绘制，
 * 看起来就像整段文字突然出现。这里仅平滑展示速度，不伪造 Agent 执行进度。
 */
async function enqueueTextWithTypingEffect(
  controller: ReadableStreamDefaultController<UIMessageChunk>,
  textPartId: string,
  text: string,
  signal?: AbortSignal,
) {
  const chunks = splitTextForTyping(text)
  const animate = shouldAnimateTyping() && chunks.length > 1
  if (!animate) {
    controller.enqueue({ type: 'text-delta', id: textPartId, delta: text })
    return
  }
  for (const chunk of chunks) {
    if (signal?.aborted) throw new DOMException('The operation was aborted', 'AbortError')
    controller.enqueue({ type: 'text-delta', id: textPartId, delta: chunk })
    // 每次让出一次浏览器绘制机会，避免 React 合并全部文本更新。
    // oxlint-disable-next-line no-await-in-loop -- typing chunks must be displayed sequentially.
    await new Promise<void>((resolve) => window.setTimeout(resolve, TYPING_INTERVAL_MS))
  }
}

export function splitTextForTyping(text: string): string[] {
  const characters = Array.from(text)
  const chunks: string[] = []
  for (let start = 0; start < characters.length; start += TYPING_CHUNK_CHARACTERS) {
    chunks.push(characters.slice(start, start + TYPING_CHUNK_CHARACTERS).join(''))
  }
  return chunks
}

function shouldAnimateTyping(): boolean {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false
  if (document.visibilityState === 'hidden') return false
  return !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

export function toUiMessage(message: import('../../service/assistant').AssistantMessage): AssistantUIMessage {
  return {
    id: message.id,
    role: message.role === 'USER' ? 'user' : 'assistant',
    metadata: { answered: message.answered ?? undefined, status: message.status },
    parts: [
      { type: 'text', text: message.content, state: 'done' },
      ...(message.sources.length ? [{ type: 'data-sources' as const, data: { sources: message.sources } }] : []),
    ],
  }
}

export function getMessageText(message?: UIMessage): string {
  return message?.parts.filter((part) => part.type === 'text').map((part) => part.text).join('') ?? ''
}

export function getSources(message: AssistantUIMessage): RegulationAnswerSource[] {
  const part = message.parts.find((item) => item.type === 'data-sources')
  return part?.data.sources ?? []
}
