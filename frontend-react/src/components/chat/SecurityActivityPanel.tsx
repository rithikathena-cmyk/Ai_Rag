import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, ChevronDown, CircleCheck, CircleDashed, CircleX, EyeOff, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import {
  buildActivityTimeline,
  summarizeChecks,
  STATUS_RANK,
  type ActivityItem,
  type ActivitySectionGroup,
  type ActivityStatus,
} from '@/lib/guardrails'
import { ChecksScoreBar } from '@/components/ui/ChecksScoreBar'
import { cn } from '@/lib/cn'
import type { ChatTraceStep } from '@/types/chat'

const STATUS_STYLES: Record<ActivityStatus, string> = {
  PASSED: 'text-emerald-600',
  DETECTED: 'text-amber-600',
  FLAGGED: 'text-amber-600',
  REDACTED: 'text-amber-600',
  BLOCKED: 'text-red-600',
  SKIPPED: 'text-neutral-300',
}

const STATUS_ICON: Record<ActivityStatus, typeof CircleCheck> = {
  PASSED: CircleCheck,
  DETECTED: AlertTriangle,
  FLAGGED: AlertTriangle,
  REDACTED: EyeOff,
  BLOCKED: CircleX,
  SKIPPED: CircleDashed,
}

function CheckRow({ item }: { item: ActivityItem }) {
  const Icon = STATUS_ICON[item.status]
  const statusLabel = item.status.charAt(0) + item.status.slice(1).toLowerCase()
  return (
    <div className={cn('flex items-start gap-2 text-xs', STATUS_STYLES[item.status])}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 text-neutral-600">
        <span className={cn('font-medium', STATUS_STYLES[item.status])}>{item.label}</span>
        <span className="text-neutral-400"> · {statusLabel}</span>
        {item.message && <span className="mt-0.5 block text-neutral-500">{item.message}</span>}
      </span>
    </div>
  )
}

// The RAG & Access Security section is never a collapsible group — access
// authorization, agent routing, and each tool call are exactly the
// distinguishing "what happened" signals this panel exists to surface, so
// they always render as individual, always-visible rows.
function UngroupedSection({ section }: { section: ActivitySectionGroup }) {
  return (
    <div className="space-y-2.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-400">{section.title}</p>
      <div className="grid gap-x-4 gap-y-2.5 [grid-template-columns:repeat(auto-fit,minmax(200px,1fr))]">
        {section.items.map((item) => (
          <CheckRow key={item.key} item={item} />
        ))}
      </div>
    </div>
  )
}

// input/llm/output sections collapse into one accordion group — defaults
// OPEN the moment anything inside isn't a plain PASSED, so a real failure is
// never left an extra click away, and defaults CLOSED when everything passed,
// keeping the common case scannable.
function CollapsibleSection({ section }: { section: ActivitySectionGroup }) {
  const worst = section.items.reduce((a, b) => (STATUS_RANK[b.status] > STATUS_RANK[a.status] ? b : a))
  const [open, setOpen] = useState(worst.status !== 'PASSED')

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 py-1 text-left"
      >
        <ChevronDown className={cn('h-3 w-3 shrink-0 text-neutral-400 transition-transform', open && 'rotate-180')} />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
          {section.title} ({section.items.length} {section.items.length === 1 ? 'check' : 'checks'})
        </span>
        <span className={cn('ml-auto text-[11px] font-medium', STATUS_STYLES[worst.status])}>
          {worst.status.charAt(0) + worst.status.slice(1).toLowerCase()}
        </span>
      </button>
      {open && (
        <div className="animate-fade-slide-up mt-2 grid gap-x-4 gap-y-2.5 border-l border-neutral-200 pl-3 [grid-template-columns:repeat(auto-fit,minmax(200px,1fr))]">
          {section.items.map((item) => (
            <CheckRow key={item.key} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}

export function SecurityActivityPanel({
  trace,
  responseTimeMs,
  open,
  onClose,
}: {
  trace: ChatTraceStep[]
  responseTimeMs?: number
  open: boolean
  onClose: () => void
}) {
  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const timeline = buildActivityTimeline(trace)
  const blocked = timeline.finalStatus === 'BLOCKED'
  const duration = responseTimeMs != null ? `${(responseTimeMs / 1000).toFixed(2)}s` : null
  const checksSummary = summarizeChecks(timeline)

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close security & activity panel"
        onClick={onClose}
        className="animate-fade-in absolute inset-0 bg-neutral-900/30"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Security & Activity"
        className="animate-fade-slide-up relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-y-auto rounded-2xl border border-neutral-200 bg-surface shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-neutral-200 p-5">
          <div className="flex items-start gap-3">
            <div
              className={cn(
                'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
                blocked ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600',
              )}
            >
              {blocked ? <ShieldAlert className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
            </div>
            <div>
              <h3 className="text-sm font-semibold text-ink">Security &amp; Activity</h3>
              <p className={cn('mt-0.5 text-xs font-medium', blocked ? 'text-red-600' : 'text-emerald-600')}>
                {timeline.finalStatus}
                {!blocked && duration ? ` · ${duration}` : ''}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md p-1.5 text-neutral-400 transition-colors hover:bg-neutral-100 hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <ChecksScoreBar summary={checksSummary} className="border-b border-neutral-200 px-5 py-4" />

        <div className="space-y-5 p-5">
          {timeline.sections.map((section) =>
            section.key === 'rag' ? (
              <UngroupedSection key={section.key} section={section} />
            ) : (
              <CollapsibleSection key={section.key} section={section} />
            ),
          )}

          <div className="flex items-center justify-between border-t border-neutral-200 pt-3 text-xs">
            <span className="font-medium text-neutral-500">Final decision</span>
            <span className={cn('font-semibold', blocked ? 'text-red-600' : 'text-emerald-600')}>{timeline.finalStatus}</span>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
