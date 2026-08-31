/** 为每个前端 HTTP 请求生成可与后端日志关联的独立 ID。 */
export function createRequestId(): string {
  // randomUUID 只保证在安全上下文可用；内网 HTTP 部署时使用格式安全的
  // 关联 ID 降级，避免请求在真正发出前就因浏览器能力不足而失败。
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}
