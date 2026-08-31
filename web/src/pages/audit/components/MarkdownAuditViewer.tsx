import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from 'react'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'
import { Button, Empty } from 'antd'
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import type { AuditEvidenceResponse } from '../../../service/audit-task'

interface MarkdownAuditViewerProps {
  content: string
  contentStart: number
  unitNumber: number
  totalUnits: number
  evidences: AuditEvidenceResponse[]
  selectedBlockIds: string[]
  onUnitChange: (unit: number) => void
}

interface EvidenceRange {
  start: number
  end: number
  selected: boolean
}

export function buildMarkdownEvidenceRanges(
  evidences: AuditEvidenceResponse[],
  contentStart: number,
  selectedIds: ReadonlySet<string>,
): EvidenceRange[] {
  return evidences.flatMap((evidence) => {
    if (evidence.charStart === null || evidence.charEnd === null) return []
    return [{
      start: evidence.charStart - contentStart,
      end: evidence.charEnd - contentStart,
      selected: evidence.documentBlockId !== null && selectedIds.has(evidence.documentBlockId),
    }]
  })
}

const EvidenceRangesContext = createContext<EvidenceRange[]>([])

function useEvidenceAttributes(node: ExtraProps['node']) {
  const ranges = useContext(EvidenceRangesContext)
  const start = node?.position?.start.offset
  const end = node?.position?.end.offset
  if (start === undefined || end === undefined) return {}
  const overlaps = ranges.filter((range) => range.start < end && range.end > start)
  if (!overlaps.length) return {}
  const selected = overlaps.some((range) => range.selected)
  return {
    className: `markdown-evidence${selected ? ' is-selected' : ''}`,
    'data-selected': selected ? 'true' : undefined,
  }
}

type MarkdownElementProps<Tag extends keyof HTMLElementTagNameMap> =
  ComponentPropsWithoutRef<Tag> & ExtraProps & { children?: ReactNode }

function MarkdownParagraph({ node, children, ...props }: MarkdownElementProps<'p'>) {
  return <p {...props} {...useEvidenceAttributes(node)}>{children}</p>
}
function MarkdownHeading1({ node, children, ...props }: MarkdownElementProps<'h1'>) {
  return <h1 {...props} {...useEvidenceAttributes(node)}>{children}</h1>
}
function MarkdownHeading2({ node, children, ...props }: MarkdownElementProps<'h2'>) {
  return <h2 {...props} {...useEvidenceAttributes(node)}>{children}</h2>
}
function MarkdownHeading3({ node, children, ...props }: MarkdownElementProps<'h3'>) {
  return <h3 {...props} {...useEvidenceAttributes(node)}>{children}</h3>
}
function MarkdownHeading4({ node, children, ...props }: MarkdownElementProps<'h4'>) {
  return <h4 {...props} {...useEvidenceAttributes(node)}>{children}</h4>
}
function MarkdownHeading5({ node, children, ...props }: MarkdownElementProps<'h5'>) {
  return <h5 {...props} {...useEvidenceAttributes(node)}>{children}</h5>
}
function MarkdownHeading6({ node, children, ...props }: MarkdownElementProps<'h6'>) {
  return <h6 {...props} {...useEvidenceAttributes(node)}>{children}</h6>
}
function MarkdownUnorderedList({ node, children, ...props }: MarkdownElementProps<'ul'>) {
  return <ul {...props} {...useEvidenceAttributes(node)}>{children}</ul>
}
function MarkdownOrderedList({ node, children, ...props }: MarkdownElementProps<'ol'>) {
  return <ol {...props} {...useEvidenceAttributes(node)}>{children}</ol>
}
function MarkdownBlockquote({ node, children, ...props }: MarkdownElementProps<'blockquote'>) {
  return <blockquote {...props} {...useEvidenceAttributes(node)}>{children}</blockquote>
}
function MarkdownPre({ node, children, ...props }: MarkdownElementProps<'pre'>) {
  return <pre {...props} {...useEvidenceAttributes(node)}>{children}</pre>
}
function MarkdownTable({ node, children, ...props }: MarkdownElementProps<'table'>) {
  const attributes = useEvidenceAttributes(node)
  return <div className={`markdown-table-wrap ${attributes.className ?? ''}`} data-selected={attributes['data-selected']}><table {...props}>{children}</table></div>
}
function SafeMarkdownImage({ alt }: MarkdownElementProps<'img'>) {
  const { t } = useTranslation()
  // 第三方 Markdown 中的远程图片不自动请求，避免跟踪像素和信息外泄。
  return <span className="markdown-image-placeholder">{t('audit.viewer.image', { name: alt || t('audit.viewer.unnamed') })}</span>
}
function SafeMarkdownLink({ node: _node, children, ...props }: MarkdownElementProps<'a'>) {
  return <a {...props} target="_blank" rel="noreferrer">{children}</a>
}

const markdownComponents: Components = {
  p: MarkdownParagraph,
  h1: MarkdownHeading1,
  h2: MarkdownHeading2,
  h3: MarkdownHeading3,
  h4: MarkdownHeading4,
  h5: MarkdownHeading5,
  h6: MarkdownHeading6,
  ul: MarkdownUnorderedList,
  ol: MarkdownOrderedList,
  blockquote: MarkdownBlockquote,
  table: MarkdownTable,
  pre: MarkdownPre,
  img: SafeMarkdownImage,
  a: SafeMarkdownLink,
}

export function MarkdownAuditViewer({ content, contentStart, unitNumber, totalUnits, evidences, selectedBlockIds, onUnitChange }: MarkdownAuditViewerProps) {
  const { t } = useTranslation()
  const viewportRef = useRef<HTMLDivElement>(null)
  const selectedIds = useMemo(() => new Set(selectedBlockIds), [selectedBlockIds])
  const selectionRevision = selectedBlockIds.join('|')
  const ranges = useMemo(
    () => buildMarkdownEvidenceRanges(evidences, contentStart, selectedIds),
    [contentStart, evidences, selectedIds],
  )

  useEffect(() => {
    if (!selectionRevision) return
    viewportRef.current?.querySelector('[data-selected="true"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [selectionRevision])

  return <div className="markdown-audit-viewer">
    <div className="markdown-toolbar">
      <Button size="small" icon={<LeftOutlined />} disabled={unitNumber <= 1} onClick={() => onUnitChange(unitNumber - 1)} />
      <span>{t('audit.viewer.sectionCounter', { current: unitNumber, total: totalUnits || '-' })}</span>
      <Button size="small" icon={<RightOutlined />} disabled={unitNumber >= totalUnits} onClick={() => onUnitChange(unitNumber + 1)} />
    </div>
    <div ref={viewportRef} className="markdown-stage">
      {content ? <article className="markdown-document"><EvidenceRangesContext value={ranges}><ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{content}</ReactMarkdown></EvidenceRangesContext></article> : <Empty description={t('audit.viewer.sectionUnavailable')} />}
    </div>
  </div>
}
