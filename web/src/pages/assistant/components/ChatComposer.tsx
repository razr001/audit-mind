import { useRef, useState } from 'react'
import { Button, Input } from 'antd'
import { ArrowUpOutlined, StopOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

interface ChatComposerProps {
  busy: boolean
  onSend: (content: string) => Promise<void>
  onStop: () => void
}

export function ChatComposer({ busy, onSend, onStop }: ChatComposerProps) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const submittingRef = useRef(false)
  const submit = async () => {
    const content = value.trim()
    if (!content || busy || submittingRef.current) return
    submittingRef.current = true
    setValue('')
    try {
      await onSend(content)
    } catch {
      setValue((current) => current || content)
    } finally {
      submittingRef.current = false
    }
  }

  return (
    <div className="mx-auto w-full max-w-[860px] px-5 pb-5 max-[760px]:px-3 max-[760px]:pb-3">
      <div className="rounded-[18px] border border-[var(--line)] bg-[rgba(13,28,23,.96)] p-2.5 shadow-[0_18px_50px_rgba(0,0,0,.2)] focus-within:border-[rgba(128,241,198,.4)] group-data-[theme=light]/app:bg-white group-data-[theme=light]/app:shadow-[0_16px_45px_rgba(29,61,49,.1)]">
        <Input.TextArea
          className="assistant-composer-input"
          variant="borderless"
          value={value}
          autoSize={{ minRows: 1, maxRows: 7 }}
          maxLength={1000}
          placeholder={t('assistant.placeholder')}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit()
            }
          }}
        />
        <div className="mt-1 flex items-center justify-between pl-2">
          <span className="text-[10px] text-[var(--muted)]">{t('assistant.knowledgeHint')}</span>
          {busy ? (
            <Button className="!h-9 !w-9 !rounded-full !p-0" icon={<StopOutlined />} onClick={onStop} aria-label={t('assistant.stop')} />
          ) : (
            <Button className="!h-9 !w-9 !rounded-full !p-0" type="primary" icon={<ArrowUpOutlined />} disabled={!value.trim()} onClick={() => void submit()} aria-label={t('assistant.send')} />
          )}
        </div>
      </div>
      <p className="mt-2 mb-0 text-center text-[9px] text-[var(--muted)]">{t('assistant.disclaimer')}</p>
    </div>
  )
}
