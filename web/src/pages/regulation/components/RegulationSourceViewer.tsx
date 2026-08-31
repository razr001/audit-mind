import { FileTextOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Spin } from 'antd'
import { useEffect, useState, type ComponentPropsWithoutRef } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { RegulationParseBlockResponse } from '../../../service/regulation-processing'
import { RegulationPdfViewer } from './RegulationPdfViewer'
import { useTranslation } from 'react-i18next'

interface RegulationSourceViewerProps {
  url?: string
  contentType?: string
  filename: string
  loading: boolean
  error: boolean
  pageNumber: number
  pageCount: number
  blocks: RegulationParseBlockResponse[]
  selectedBlockIds: string[]
  blocksLoading: boolean
  onPageChange: (pageNumber: number) => void
  onRefresh: () => void
}

function SafeMarkdownImage({ alt }: ComponentPropsWithoutRef<'img'>) {
  const { t } = useTranslation()
  // 不自动加载知识文本中的远程图片，避免跟踪像素和无意的外部请求。
  return <span className="regulation-markdown-image">{t('regulation.viewer.image', { name: alt || t('regulation.viewer.unnamed') })}</span>
}

const markdownComponents: Components = {
  img: SafeMarkdownImage,
  a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a>,
}

export function RegulationSourceViewer({ url, contentType, filename, loading, error, pageNumber, pageCount, blocks, selectedBlockIds, blocksLoading, onPageChange, onRefresh }: RegulationSourceViewerProps) {
  const { t } = useTranslation()
  const isPdf = contentType?.toLowerCase().includes('pdf') ?? filename.toLowerCase().endsWith('.pdf')
  const [textResult, setTextResult] = useState<{ url: string; text?: string; error?: boolean }>()

  useEffect(() => {
    if (!url || isPdf) return
    const controller = new AbortController()
    void fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`source returned HTTP ${response.status}`)
        return response.text()
      })
      .then((text) => setTextResult({ url, text }))
      .catch((fetchError: unknown) => {
        if (!(fetchError instanceof DOMException && fetchError.name === 'AbortError')) setTextResult({ url, error: true })
      })
    return () => controller.abort()
  }, [isPdf, url])
  const currentTextResult = textResult?.url === url ? textResult : undefined
  const text = currentTextResult?.text
  const textError = currentTextResult?.error === true

  return <section className="regulation-source-pane">
    <header className="regulation-workbench-heading">
      <div className="regulation-workbench-title"><span className="regulation-workbench-icon"><FileTextOutlined /></span><div><strong>{t('regulation.viewer.title')}</strong><span>{filename}</span></div></div>
      <Button size="small" icon={<ReloadOutlined />} onClick={onRefresh}>{t('regulation.viewer.refreshUrl')}</Button>
    </header>
    <div className="regulation-source-stage">
      {loading && <div className="regulation-pane-state"><Spin /><span>{t('regulation.viewer.fetchingUrl')}</span></div>}
      {!loading && (error || textError) && <div className="regulation-pane-state"><Alert type="error" showIcon title={t('regulation.viewer.loadFailed')} description={t('regulation.viewer.expired')} action={<Button onClick={onRefresh}>{t('common.reload')}</Button>} /></div>}
      {!loading && !error && !url && <div className="regulation-pane-state"><Empty description={t('regulation.viewer.unavailable')} /></div>}
      {!loading && !error && url && isPdf && <RegulationPdfViewer url={url} pageNumber={pageNumber} pageCount={pageCount} blocks={blocks} selectedBlockIds={selectedBlockIds} blocksLoading={blocksLoading} onPageChange={onPageChange} onRefreshUrl={onRefresh} />}
      {!loading && !error && url && !isPdf && text === undefined && !textError && <div className="regulation-pane-state"><Spin /><span>{t('regulation.viewer.readingText')}</span></div>}
      {!loading && !error && url && !isPdf && text !== undefined && <article className="regulation-markdown-document"><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{text}</ReactMarkdown></article>}
    </div>
  </section>
}
