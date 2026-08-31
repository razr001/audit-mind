import { useEffect, useMemo, useRef, useState } from 'react'
import { LeftOutlined, RedoOutlined, RightOutlined, RotateLeftOutlined, RotateRightOutlined } from '@ant-design/icons'
import { Button, Empty, Spin } from 'antd'
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import { rotateBox } from '../../../lib/pdf-coordinates'
import type { RegulationParseBlockResponse } from '../../../service/regulation-processing'
import { useTranslation } from 'react-i18next'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

interface RegulationPdfViewerProps {
  url: string
  pageNumber: number
  pageCount: number
  blocks: RegulationParseBlockResponse[]
  selectedBlockIds: string[]
  blocksLoading: boolean
  onPageChange: (pageNumber: number) => void
  onRefreshUrl: () => void
}

export function RegulationPdfViewer({ url, pageNumber, pageCount, blocks, selectedBlockIds, blocksLoading, onPageChange, onRefreshUrl }: RegulationPdfViewerProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)
  const [pdfResult, setPdfResult] = useState<{ url: string; pdf?: PDFDocumentProxy; failed?: boolean }>()
  const [rotation, setRotation] = useState(0)
  const [scale, setScale] = useState(1.15)
  const [renderedView, setRenderedView] = useState<string>()
  const [failedView, setFailedView] = useState<string>()
  const currentPdfResult = pdfResult?.url === url ? pdfResult : undefined
  const pdf = currentPdfResult?.pdf
  const viewKey = `${url}:${pageNumber}:${rotation}:${scale}`
  const loading = !currentPdfResult || Boolean(pdf && renderedView !== viewKey)
  const loadFailed = currentPdfResult?.failed === true || failedView === viewKey
  const selected = useMemo(() => new Set(selectedBlockIds), [selectedBlockIds])
  const highlightedBlocks = useMemo(() => blocks.filter((block) => selected.has(block.id) && block.bbox), [blocks, selected])

  useEffect(() => {
    let cancelled = false
    const task = getDocument({ url, withCredentials: false })
    void task.promise.then((document) => {
      if (cancelled) { void task.destroy(); return }
      setPdfResult({ url, pdf: document })
    }).catch(() => { if (!cancelled) setPdfResult({ url, failed: true }) })
    return () => { cancelled = true; renderTaskRef.current?.cancel(); void task.destroy() }
  }, [url])

  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let cancelled = false
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
    }).then(() => {
      if (!cancelled) setRenderedView(viewKey)
    }).catch((renderError: unknown) => {
      // 翻页、旋转和缩放会取消上一次渲染，这不是文件加载错误。
      if (!cancelled && renderError instanceof Error && renderError.name !== 'RenderingCancelledException') setFailedView(viewKey)
    })
    return () => { cancelled = true; renderTaskRef.current?.cancel() }
  }, [pageNumber, pdf, rotation, scale, viewKey])

  useEffect(() => {
    const stage = stageRef.current
    const firstBox = highlightedBlocks[0]?.bbox
    const box = firstBox ? rotateBox(firstBox, rotation) : null
    if (!stage || !box || renderedView !== viewKey || blocksLoading) return
    const canvas = canvasRef.current
    if (!canvas) return
    stage.scrollTo({
      left: Math.max(0, canvas.offsetLeft + canvas.clientWidth * box[0] / 1000 - stage.clientWidth / 2),
      top: Math.max(0, canvas.offsetTop + canvas.clientHeight * box[1] / 1000 - stage.clientHeight / 3),
      behavior: 'smooth',
    })
  }, [blocksLoading, highlightedBlocks, renderedView, rotation, viewKey])

  const totalPages = pdf?.numPages ?? pageCount
  return <div className="regulation-pdf-viewer">
    <div className="regulation-pdf-toolbar">
      <Button size="small" icon={<LeftOutlined />} disabled={pageNumber <= 1} onClick={() => onPageChange(pageNumber - 1)} />
      <span>{t('regulation.viewer.pageCounter', { current: pageNumber, total: totalPages || '-' })}</span>
      <Button size="small" icon={<RightOutlined />} disabled={!totalPages || pageNumber >= totalPages} onClick={() => onPageChange(pageNumber + 1)} />
      <span className="regulation-pdf-toolbar-divider" />
      <Button size="small" icon={<RotateLeftOutlined />} onClick={() => setRotation((value) => (value + 270) % 360)} />
      <Button size="small" icon={<RotateRightOutlined />} onClick={() => setRotation((value) => (value + 90) % 360)} />
      <Button size="small" onClick={() => setScale((value) => Math.max(.65, value - .15))}>−</Button>
      <span>{Math.round(scale * 100)}%</span>
      <Button size="small" onClick={() => setScale((value) => Math.min(2.5, value + .15))}>+</Button>
      <Button size="small" icon={<RedoOutlined />} onClick={onRefreshUrl}>{t('regulation.viewer.refreshUrl')}</Button>
    </div>
    <div ref={stageRef} className="regulation-pdf-stage">
      {(loading || blocksLoading) && <Spin className="regulation-pdf-loading" />}
      {loadFailed && !loading && <div className="regulation-pane-state"><Empty description={t('regulation.viewer.pdfLoadFailed')} /></div>}
      <div className="regulation-pdf-page">
        <canvas ref={canvasRef} />
        <div className="regulation-pdf-highlight-layer">
          {highlightedBlocks.map((block) => {
            const box = block.bbox ? rotateBox(block.bbox, rotation) : null
            if (!box) return null
            return <span key={block.id} className="regulation-pdf-highlight" style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`, width: `${(box[2] - box[0]) / 10}%`, height: `${(box[3] - box[1]) / 10}%` }} title={block.content} />
          })}
        </div>
      </div>
    </div>
  </div>
}
