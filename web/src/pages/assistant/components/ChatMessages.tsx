import { useEffect, useRef, useState } from 'react'
import { Button, Skeleton, Tooltip } from 'antd'
import { CheckOutlined, CopyOutlined, FileSearchOutlined, RobotOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AssistantUIMessage } from '../assistant-transport'
import type { AssistantMessageStatus } from '../../../service/assistant'
import { getMessageText, getSources } from '../assistant-transport'
import type { RegulationAnswerPhase } from '../../../service/regulation-queries'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

interface ChatMessagesProps {
  messages: AssistantUIMessage[]
  loading: boolean
  busy: boolean
  phase?: RegulationAnswerPhase
}

function getPhaseLabels(t: TFunction): Record<RegulationAnswerPhase, string> {
  return { guarding: t('assistant.phase.guarding'), understanding: t('assistant.phase.understanding'), retrieving: t('assistant.phase.retrieving'), reranking: t('assistant.phase.reranking'), 'screening-context': t('assistant.phase.screeningContext'), generating: t('assistant.phase.generating'), validating: t('assistant.phase.validating'), 'screening-output': t('assistant.phase.screeningOutput') }
}

export function ChatMessages({ messages, loading, busy, phase }: ChatMessagesProps) {
  const { t } = useTranslation()
  const phaseLabels = getPhaseLabels(t)
  const viewportRef = useRef<HTMLDivElement>(null)
  const nearBottomRef = useRef(true)
  const messageRevision = messages.map((message) => `${message.id}:${getMessageText(message).length}:${message.parts.length}`).join('|')

  useEffect(() => {
    if (!messageRevision) return
    if (!nearBottomRef.current) return
    const viewport = viewportRef.current
    if (viewport) viewport.scrollTop = viewport.scrollHeight
  }, [messageRevision])

  return (
    <div
      ref={viewportRef}
      className="min-h-0 flex-1 overflow-y-auto"
      onScroll={(event) => {
        const element = event.currentTarget
        nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120
      }}
    >
      <div className="mx-auto min-h-full max-w-[860px] px-5 py-8 max-[760px]:px-3 max-[760px]:py-5">
        {loading && <Skeleton active paragraph={{ rows: 6 }} />}
        {!loading && messages.length === 0 && <AssistantEmpty />}
        {messages.map((message) => <MessageItem key={message.id} message={message} />)}
        {busy && phase && (
          <div className="mt-5 flex items-center gap-3 text-xs text-[var(--muted)]">
            <span className="assistant-thinking-dot" /><span>{phaseLabels[phase]}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function AssistantEmpty() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-[58vh] flex-col items-center justify-center text-center">
      <div className="mb-5 grid h-14 w-14 place-items-center rounded-[17px] border border-[rgba(128,241,198,.28)] bg-[rgba(128,241,198,.08)] text-2xl text-[var(--mint)]"><RobotOutlined /></div>
      <h1 className="m-0 font-['Manrope'] text-[26px] font-semibold tracking-[-.7px]">{t('assistant.emptyTitle')}</h1>
      <p className="mt-2 mb-7 max-w-[480px] text-xs leading-6 text-[var(--muted)]">{t('assistant.emptySubtitle')}</p>
    </div>
  )
}

function MessageItem({ message }: { message: AssistantUIMessage }) {
  const { t } = useTranslation()
  const content = getMessageText(message)
  const sources = getSources(message)
  const user = message.role === 'user'
  return (
    <article className={`mb-7 flex ${user ? 'justify-end' : 'justify-start'}`}>
      <div className={user ? 'max-w-[78%] rounded-[16px_16px_4px_16px] bg-[rgba(128,241,198,.13)] px-4 py-3 text-[13px] leading-6 group-data-[theme=light]/app:bg-[#e4f1eb]' : 'w-full'}>
        {!user && <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold text-[var(--mint)]"><span className="grid h-7 w-7 place-items-center rounded-[9px] bg-[rgba(128,241,198,.09)]"><RobotOutlined /></span>{t('assistant.name')}</div>}
        {user ? <p className="m-0 whitespace-pre-wrap">{content}</p> : content ? <div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div> : <p className="m-0 text-xs text-[var(--muted)]">{emptyAssistantMessage(message.metadata?.status, t)}</p>}
        {!user && content && <div className="mt-3 flex items-center gap-1"><CopyButton content={content} />{message.metadata?.answered && <Tooltip title={t('assistant.sourceVerifiedTip')}><span className="ml-1 text-[11px] text-[var(--mint)]"><CheckOutlined /> {t('assistant.sourceVerified')}</span></Tooltip>}</div>}
        {!user && sources.length > 0 && <div className="mt-4 border-t border-[var(--line)] pt-3"><p className="mb-2 text-[10px] font-semibold tracking-[.6px] text-[var(--muted)]"><FileSearchOutlined /> {t('assistant.sources', { count: sources.length })}</p><div className="space-y-2">{sources.map((source, index) => <details key={`${source.chunkId}-${source.quote}`} className="rounded-[10px] border border-[var(--line)] bg-black/[.08] px-3 py-2 group-data-[theme=light]/app:bg-white/50"><summary className="cursor-pointer text-[11px] font-medium">[{index + 1}] {source.title}{source.pageStart || source.pageNumber ? ` · ${t('assistant.sourcePage', { page: source.pageStart ?? source.pageNumber })}` : ''}</summary><blockquote className="mt-2 mb-1 border-l-2 border-[var(--mint)] pl-3 text-[11px] leading-5 text-[var(--muted)]">{source.quote}</blockquote></details>)}</div></div>}
      </div>
    </article>
  )
}

function emptyAssistantMessage(status: AssistantMessageStatus | undefined, t: TFunction) {
  if (status === undefined) return t('assistant.generating')
  if (status === 'GENERATING') return t('assistant.unfinished')
  if (status === 'CANCELED') return t('assistant.canceled')
  return t('assistant.failed')
}

function CopyButton({ content }: { content: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return <Tooltip title={t(copied ? 'assistant.copied' : 'assistant.copy')}><Button type="text" size="small" icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={() => { void navigator.clipboard.writeText(content); setCopied(true); window.setTimeout(() => setCopied(false), 1200) }} /></Tooltip>
}
