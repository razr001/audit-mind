import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Empty, Spin } from 'antd'
import { LeftOutlined, RedoOutlined, RightOutlined, RotateLeftOutlined, RotateRightOutlined } from '@ant-design/icons'
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import type { AuditEvidenceResponse } from '../../../service/audit-task'
import { rotateBox } from './pdf-coordinates'
import { useTranslation } from 'react-i18next'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

interface PdfAuditViewerProps {
  url: string
  pageNumber: number
  evidences: AuditEvidenceResponse[]
  selectedBlockIds: string[]
  onPageChange: (page: number) => void
  onRefreshUrl: () => void
}

export function PdfAuditViewer({ url, pageNumber, evidences, selectedBlockIds, onPageChange, onRefreshUrl }: PdfAuditViewerProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)
  const [pdf, setPdf] = useState<PDFDocumentProxy>()
  const [rotation, setRotation] = useState(0)
  const [scale, setScale] = useState(1.25)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const selected = useMemo(() => new Set(selectedBlockIds), [selectedBlockIds])

  useEffect(() => {
    let cancelled = false
    const task = getDocument({ url, withCredentials: false })
    void task.promise.then((document) => {
      if (cancelled) { void task.destroy(); return }
      setPdf(document)
    }).catch(() => { if (!cancelled) setLoadFailed(true) }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; void task.destroy() }
  }, [url])

  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let cancelled = false
    setLoading(true)
    void pdf.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return
      const viewport = page.getViewport({ scale, rotation })
      const canvas = canvasRef.current
      const context = canvas.getContext('2d')
      if (!context) return
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.floor(viewport.width * ratio)
      canvas.height = Math.floor(viewport.height * ratio)
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      renderTaskRef.current?.cancel()
      const renderTask = page.render({ canvas, canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] })
      renderTaskRef.current = renderTask
      return renderTask.promise
    }).catch((error: unknown) => {
      // 翻页或缩放会主动取消旧渲染，RenderingCancelledException 不是用户可见错误。
      if (error instanceof Error && error.name !== 'RenderingCancelledException') setLoadFailed(true)
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; renderTaskRef.current?.cancel() }
  }, [pageNumber, pdf, rotation, scale])

  if (!url) return <Empty description={t('audit.viewer.pdfUnavailable')} />
  return <div className="pdf-audit-viewer">
    <div className="pdf-toolbar">
      <Button icon={<LeftOutlined />} disabled={pageNumber <= 1} onClick={() => onPageChange(pageNumber - 1)} />
      <span>{t('audit.viewer.pageCounter', { current: pageNumber, total: pdf?.numPages ?? '-' })}</span>
      <Button icon={<RightOutlined />} disabled={!pdf || pageNumber >= pdf.numPages} onClick={() => onPageChange(pageNumber + 1)} />
      <Button icon={<RotateLeftOutlined />} onClick={() => setRotation((value) => (value + 270) % 360)} />
      <Button icon={<RotateRightOutlined />} onClick={() => setRotation((value) => (value + 90) % 360)} />
      <Button onClick={() => setScale((value) => Math.max(.65, value - .15))}>−</Button><span>{Math.round(scale * 100)}%</span><Button onClick={() => setScale((value) => Math.min(2.5, value + .15))}>+</Button>
      <Button icon={<RedoOutlined />} onClick={onRefreshUrl}>{t('audit.viewer.refreshUrl')}</Button>
    </div>
    <div className="pdf-stage">
      {loading && <Spin className="pdf-loading" />}
      {loadFailed && !loading && <Empty description={t('audit.viewer.pdfLoadFailed')} />}
      <div className="pdf-page-wrap"><canvas ref={canvasRef} /><div className="pdf-highlight-layer">
        {evidences.map((evidence) => {
          const box = evidence.bbox && rotateBox(evidence.bbox, rotation)
          if (!box) return null
          const isSelected = evidence.documentBlockId ? selected.has(evidence.documentBlockId) : false
          return <span key={evidence.id} className={`pdf-highlight ${isSelected ? 'is-selected' : ''}`} style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`, width: `${(box[2] - box[0]) / 10}%`, height: `${(box[3] - box[1]) / 10}%` }} title={evidence.quote} />
        })}
      </div></div>
    </div>
  </div>
}
