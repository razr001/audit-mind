import { describe, expect, it, vi } from 'vitest'
import type { UIMessageChunk } from 'ai'
import { AssistantChatTransport, type AssistantUIMessage } from './assistant-transport'

const streamAssistantMessage = vi.hoisted(() => vi.fn())

vi.mock('../../service/assistant', () => ({ streamAssistantMessage }))

const userMessage: AssistantUIMessage = {
  id: 'user-1',
  role: 'user',
  parts: [{ type: 'text', text: '测试问题' }],
}

async function readAll(stream: ReadableStream<UIMessageChunk>) {
  const result: UIMessageChunk[] = []
  const reader = stream.getReader()
  while (true) {
    // oxlint-disable-next-line no-await-in-loop -- stream chunks must be consumed in order.
    const item = await reader.read()
    if (item.done) return result
    result.push(item.value)
  }
}

function streamOptions() {
  return {
    trigger: 'submit-message' as const,
    chatId: 'assistant-workspace',
    messageId: undefined,
    messages: [userMessage],
    abortSignal: undefined,
  }
}

describe('AssistantChatTransport', () => {
  it('creates the first turn without a conversation id and keeps the returned id internally', async () => {
    streamAssistantMessage.mockImplementation(async function* () {
      yield {
        type: 'message-start',
        data: {
          conversationId: 'conversation-1',
          userMessageId: 'user-1',
          assistantMessageId: 'assistant-1',
          title: '测试问题',
        },
      }
      yield { type: 'text-delta', data: { textDelta: '回答' } }
      yield { type: 'done', data: {} }
    })
    const transport = new AssistantChatTransport()

    const firstChunks = await readAll(await transport.sendMessages(streamOptions()))
    await readAll(await transport.sendMessages(streamOptions()))

    expect(streamAssistantMessage.mock.calls[0]?.[0]).toBeUndefined()
    expect(streamAssistantMessage.mock.calls[1]?.[0]).toBe('conversation-1')
    expect(firstChunks).toContainEqual({
      type: 'data-conversation',
      data: { conversationId: 'conversation-1', title: '测试问题' },
      transient: true,
    })
    expect(firstChunks).toContainEqual({ type: 'text-delta', id: expect.any(String), delta: '回答' })
  })

  it('marks the local assistant message failed when the stream ends without a done event', async () => {
    streamAssistantMessage.mockImplementation(async function* () {
      yield {
        type: 'message-start',
        data: {
          conversationId: 'conversation-2',
          userMessageId: 'user-2',
          assistantMessageId: 'assistant-2',
          title: '异常对话',
        },
      }
    })
    const transport = new AssistantChatTransport()

    const chunks = await readAll(await transport.sendMessages(streamOptions()))

    expect(chunks).toContainEqual({
      type: 'message-metadata',
      messageMetadata: { status: 'FAILED' },
    })
    expect(chunks).toContainEqual({
      type: 'error',
      errorText: '对话连接意外中断，请重试',
    })
  })

  it('marks the local assistant message failed before reporting an SSE error', async () => {
    streamAssistantMessage.mockImplementation(async function* () {
      yield {
        type: 'message-start',
        data: {
          conversationId: 'conversation-3',
          userMessageId: 'user-3',
          assistantMessageId: 'assistant-3',
          title: '失败对话',
        },
      }
      yield { type: 'error', data: { code: 50000, message: '回答失败' } }
    })
    const transport = new AssistantChatTransport()

    const chunks = await readAll(await transport.sendMessages(streamOptions()))

    expect(chunks).toContainEqual({
      type: 'message-metadata',
      messageMetadata: { status: 'FAILED' },
    })
    expect(chunks).toContainEqual({ type: 'error', errorText: '回答失败' })
  })
})
