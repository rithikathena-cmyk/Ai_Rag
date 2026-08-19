import { Fragment, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/cn'
import type { ChatSource } from '@/types/chat'

const CITATION_PATTERN = /(\[\d+\])/g

function wrapCitations(node: ReactNode, sources: ChatSource[], onCitationClick?: (source: ChatSource) => void): ReactNode {
  if (typeof node === 'string') {
    const parts = node.split(CITATION_PATTERN)
    if (parts.length === 1) return node
    return parts.map((part, i) => {
      const match = /^\[(\d+)\]$/.exec(part)
      if (!match) return <Fragment key={i}>{part}</Fragment>
      const source = sources.find((s) => s.index === Number(match[1]))
      if (!source) return <Fragment key={i}>{part}</Fragment>
      return (
        <sup
          key={i}
          className="citation-marker"
          role="button"
          tabIndex={0}
          onClick={() => onCitationClick?.(source)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              onCitationClick?.(source)
            }
          }}
        >
          {match[1]}
        </sup>
      )
    })
  }
  if (Array.isArray(node)) {
    return node.map((child, i) => <Fragment key={i}>{wrapCitations(child, sources, onCitationClick)}</Fragment>)
  }
  return node
}

function buildCitationComponents(sources: ChatSource[], onCitationClick?: (source: ChatSource) => void): Partial<Components> {
  const wrap = (children: ReactNode) => wrapCitations(children, sources, onCitationClick)
  return {
    p: ({ children }) => <p>{wrap(children)}</p>,
    li: ({ children }) => <li>{wrap(children)}</li>,
    td: ({ children }) => <td>{wrap(children)}</td>,
  }
}

export function Markdown({
  content,
  className,
  sources,
  onCitationClick,
}: {
  content: string
  className?: string
  sources?: ChatSource[]
  onCitationClick?: (source: ChatSource) => void
}) {
  const components = sources && sources.length > 0 ? buildCitationComponents(sources, onCitationClick) : undefined
  return (
    <div className={cn('prose-chat prose max-w-none', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
