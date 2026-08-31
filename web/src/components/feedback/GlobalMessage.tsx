import { useEffect } from 'react'
import { App } from 'antd'
import './global-message.css'

type ErrorMessageHandler = (content: string) => void

let errorMessageHandler: ErrorMessageHandler | undefined
let lastError = { content: '', timestamp: 0 }

export function showGlobalError(content: string) {
  const normalized = content.trim() || '请求失败，请稍后重试'
  const now = Date.now()
  if (lastError.content === normalized && now - lastError.timestamp < 800) return
  lastError = { content: normalized, timestamp: now }
  errorMessageHandler?.(normalized)
}

export function GlobalMessageBridge() {
  const { message } = App.useApp()

  useEffect(() => {
    const handler: ErrorMessageHandler = (content) => { void message.error(content) }
    errorMessageHandler = handler
    return () => {
      if (errorMessageHandler === handler) errorMessageHandler = undefined
    }
  }, [message])

  return null
}
