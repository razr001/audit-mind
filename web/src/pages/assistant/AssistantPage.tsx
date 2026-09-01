import { useRef, useState } from 'react'
import { useChat } from '@ai-sdk/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Drawer } from 'antd'
import { HomeOutlined, MenuOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { showGlobalError } from '../../components/feedback/GlobalMessage'
import {
  deleteAssistantConversation,
  getAssistantConversations,
  getAssistantMessages,
  renameAssistantConversation,
} from '../../service/assistant'
import type { AssistantAnswerPhase } from '../../service/assistant'
import { AssistantChatTransport, toUiMessage, type AssistantUIMessage } from './assistant-transport'
import { ChatComposer } from './components/ChatComposer'
import { ChatMessages } from './components/ChatMessages'
import { ConversationHistory } from './components/ConversationHistory'
import './assistant.css'

export function AssistantPage() {
  const { t } = useTranslation()
  const [activeId, setActiveId] = useState<string>()
  const activeIdRef = useRef<string | undefined>(undefined)
  const [transport] = useState(() => new AssistantChatTransport())
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [phase, setPhase] = useState<AssistantAnswerPhase>()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const historyRequestRef = useRef(0)
  const sendingRef = useRef(false)

  const activateConversation = (conversationId?: string) => {
    activeIdRef.current = conversationId
    transport.setConversationId(conversationId)
    setActiveId(conversationId)
  }

  const conversationsQuery = useQuery({
    queryKey: ['assistant-conversations'],
    queryFn: () => getAssistantConversations({ page: 1, pageSize: 50 }),
  })
  const { messages, sendMessage, setMessages, status, stop } = useChat<AssistantUIMessage>({
    id: 'assistant-workspace',
    transport,
    throttle: 25,
    onData: (part) => {
      if (part.type === 'data-phase') setPhase(part.data.phase)
      if (part.type === 'data-conversation') {
        activateConversation(part.data.conversationId)
        void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] })
      }
    },
    onError: (error) => {
      setPhase(undefined)
      showGlobalError(error.message)
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] })
      const failedConversationId = activeIdRef.current
      if (failedConversationId) {
        void queryClient.invalidateQueries({ queryKey: ['assistant-messages', failedConversationId] })
      }
    },
    onFinish: () => {
      setPhase(undefined)
      void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] })
      const completedConversationId = activeIdRef.current
      if (completedConversationId) {
        void queryClient.invalidateQueries({ queryKey: ['assistant-messages', completedConversationId] })
      }
    },
  })
  const busy = status === 'submitted' || status === 'streaming'

  const loadConversation = async (conversationId: string) => {
    const requestId = ++historyRequestRef.current
    stop()
    setPhase(undefined)
    activateConversation(conversationId)
    setMessages([])
    setHistoryLoading(true)
    try {
      const response = await queryClient.fetchQuery({
        queryKey: ['assistant-messages', conversationId],
        queryFn: () => getAssistantMessages(conversationId, { page: 1, pageSize: 100 }),
        staleTime: 0,
      })
      if (requestId !== historyRequestRef.current || activeIdRef.current !== conversationId) return
      const items = response.data?.items ?? []
      setMessages(items.reduceRight<AssistantUIMessage[]>((result, item) => {
        result.push(toUiMessage(item))
        return result
      }, []))
    } catch {
      if (requestId === historyRequestRef.current) setMessages([])
    } finally {
      if (requestId === historyRequestRef.current) setHistoryLoading(false)
    }
  }

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameAssistantConversation(id, title),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] }),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteAssistantConversation,
    onSuccess: async (_, deletedId) => {
      void message.success(t('assistant.deleted'))
      await queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] })
      if (deletedId === activeIdRef.current) {
        historyRequestRef.current += 1
        activateConversation()
        setHistoryLoading(false)
        setMessages([])
      }
    },
  })

  const send = async (content: string) => {
    if (sendingRef.current) return
    sendingRef.current = true
    historyRequestRef.current += 1
    setHistoryLoading(false)
    setPhase('guarding')
    try {
      await sendMessage({ text: content })
    } catch (error) {
      setPhase(undefined)
      throw error
    } finally {
      sendingRef.current = false
    }
  }

  const history = (
    <ConversationHistory
      conversations={conversationsQuery.data?.data?.items ?? []}
      activeId={activeId}
      loading={conversationsQuery.isLoading}
      onNew={() => {
        historyRequestRef.current += 1
        stop()
        setPhase(undefined)
        activateConversation()
        setHistoryLoading(false)
        setMessages([])
        setHistoryOpen(false)
      }}
      onSelect={(id) => {
        if (id === activeIdRef.current) {
          setHistoryOpen(false)
          return
        }
        setHistoryOpen(false)
        void loadConversation(id)
      }}
      onRename={async (id, title) => { await renameMutation.mutateAsync({ id, title }) }}
      onDelete={async (id) => { await deleteMutation.mutateAsync(id) }}
    />
  )

  return (
    <AppLayout
      activeNavigation="assistant"
      headerStart={<div className="flex items-center gap-2"><Button className="min-[901px]:hidden" type="text" icon={<MenuOutlined />} onClick={() => setHistoryOpen(true)} /><HeaderBreadcrumb icon={<HomeOutlined />} section={t('assistant.workspace')} current={t('assistant.title')} /></div>}
    >
      <div className="flex h-[calc(100vh-78px)] min-h-[560px] overflow-hidden">
        {history}
        <section className="flex min-w-0 flex-1 flex-col bg-[radial-gradient(circle_at_50%_-20%,rgba(128,241,198,.06),transparent_34%)]">
          <ChatMessages
            messages={messages}
            loading={historyLoading}
            busy={busy}
            phase={phase}
          />
          <ChatComposer busy={busy} onSend={send} onStop={() => { setPhase(undefined); stop() }} />
        </section>
      </div>
      <Drawer className="assistant-history-drawer" title={t('assistant.history')} placement="left" size={286} open={historyOpen} onClose={() => setHistoryOpen(false)}>{history}</Drawer>
    </AppLayout>
  )
}
