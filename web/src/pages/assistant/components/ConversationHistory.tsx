import { useMemo, useState } from 'react'
import { App, Button, Dropdown, Empty, Input, Skeleton } from 'antd'
import { DeleteOutlined, EditOutlined, MoreOutlined, PlusOutlined } from '@ant-design/icons'
import type { AssistantConversation } from '../../../service/assistant'
import { useTranslation } from 'react-i18next'

interface ConversationHistoryProps {
  conversations: AssistantConversation[]
  activeId?: string
  loading: boolean
  onNew: () => void
  onSelect: (id: string) => void
  onRename: (id: string, title: string) => Promise<void>
  onDelete: (id: string) => Promise<void>
}

export function ConversationHistory({ conversations, activeId, loading, onNew, onSelect, onRename, onDelete }: ConversationHistoryProps) {
  const { t } = useTranslation()
  const { modal } = App.useApp()
  const [editingId, setEditingId] = useState<string>()
  const [draftTitle, setDraftTitle] = useState('')
  const groups = useMemo(() => groupConversations(conversations, [t('assistant.today'), t('assistant.lastSevenDays'), t('assistant.earlier')]), [conversations, t])

  const confirmDelete = (conversation: AssistantConversation) => {
    modal.confirm({
      title: t('assistant.deleteTitle'),
      content: t('assistant.deleteWarning'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okButtonProps: { danger: true },
      onOk: () => onDelete(conversation.id),
    })
  }

  return (
    <aside className="flex h-full w-[260px] shrink-0 flex-col border-r border-[var(--line)] bg-black/[.08] p-3 group-data-[theme=light]/app:bg-white/35 max-[900px]:hidden">
      <Button className="mb-4 h-10! justify-start!" type="primary" icon={<PlusOutlined />} block onClick={onNew}>{t('assistant.newConversation')}</Button>
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {loading && <Skeleton active paragraph={{ rows: 5 }} title={false} />}
        {!loading && conversations.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('assistant.emptyHistory')} />}
        {groups.map((group) => (
          <section key={group.label} className="mb-5">
            <h3 className="mb-2 px-2 text-[10px] font-medium tracking-[.8px] text-[var(--muted)]">{group.label}</h3>
            <div className="space-y-1">
              {group.items.map((conversation) => (
                <div key={conversation.id} className={`group/conversation flex min-h-10 items-center rounded-[9px] px-2 transition-colors ${activeId === conversation.id ? 'bg-[rgba(128,241,198,.11)] text-[var(--mint)]' : 'text-[var(--muted)] hover:bg-white/[.035] hover:text-[var(--text)]'}`}>
                  {editingId === conversation.id ? (
                    <Input
                      className="!h-8 !text-xs"
                      value={draftTitle}
                      maxLength={100}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onBlur={() => setEditingId(undefined)}
                      onPressEnter={() => {
                        const title = draftTitle.trim()
                        if (title) void onRename(conversation.id, title)
                        setEditingId(undefined)
                      }}
                    />
                  ) : (
                    <button className="min-w-0 flex-1 truncate border-0 bg-transparent py-2 text-left text-xs text-inherit" onClick={() => onSelect(conversation.id)} title={conversation.title}>{conversation.title}</button>
                  )}
                  {editingId !== conversation.id && (
                    <Dropdown
                      trigger={['click']}
                      menu={{ items: [
                        { key: 'rename', icon: <EditOutlined />, label: t('assistant.rename') },
                        { key: 'delete', icon: <DeleteOutlined />, label: t('common.delete'), danger: true },
                      ], onClick: ({ key, domEvent }) => {
                        domEvent.stopPropagation()
                        if (key === 'rename') { setDraftTitle(conversation.title); setEditingId(conversation.id) }
                        if (key === 'delete') confirmDelete(conversation)
                      } }}
                    >
                      <button className="grid h-7 w-7 shrink-0 place-items-center rounded-md border-0 bg-transparent text-[var(--muted)] opacity-0 hover:bg-white/[.06] group-hover/conversation:opacity-100" aria-label={t('assistant.actions')}><MoreOutlined /></button>
                    </Dropdown>
                  )}
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </aside>
  )
}

function groupConversations(conversations: AssistantConversation[], labels: [string, string, string]) {
  const now = Date.now()
  const day = 86_400_000
  const groups = [
    { label: labels[0], items: [] as AssistantConversation[] },
    { label: labels[1], items: [] as AssistantConversation[] },
    { label: labels[2], items: [] as AssistantConversation[] },
  ]
  conversations.forEach((item) => {
    const age = now - new Date(item.lastMessageAt ?? item.updatedAt).getTime()
    groups[age < day ? 0 : age < day * 7 ? 1 : 2].items.push(item)
  })
  return groups.filter((group) => group.items.length)
}
