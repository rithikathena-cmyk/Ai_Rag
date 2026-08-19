import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, Shield, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { listPiiOccurrences, revealPiiOccurrence } from '@/api/pii'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import type { PiiOccurrenceSummary } from '@/types/pii'

// How long a revealed value stays on screen before re-masking itself — see
// this file's own AUTO-HIDE requirement. Also re-masks immediately on
// unmount (leaving the row, navigating away), since it's plain component
// state — never written to localStorage/sessionStorage/a URL/a log.
const AUTO_HIDE_MS = 20_000

/** Admin/CEO-only raw-PII panel for the Traces page's expanded row. Renders
 *  nothing at all for a message with no captured occurrences (the common
 *  case — this is opt-in instrumentation, see pii.py's PIIOccurrenceRecord),
 *  and renders MASKED values only, ever, unless the viewer holds
 *  PII_VIEW_RAW and explicitly confirms per-entity — the backend enforces
 *  this independently; this component's own gating is a UX nicety, not the
 *  security boundary. */
export function PiiOccurrencesPanel({ messageId }: { messageId: string }) {
  const { hasPermission } = useAuth()
  const canReveal = hasPermission('PII_VIEW_RAW')

  const query = useQuery({
    queryKey: ['pii-occurrences', messageId],
    queryFn: () => listPiiOccurrences(messageId),
  })

  const items = query.data?.items ?? []
  if (query.isLoading || items.length === 0) return null

  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
        <Shield className="h-3 w-3" /> PII Detection
      </p>
      <div className="overflow-hidden rounded-lg border border-neutral-200 bg-surface">
        {items.map((item) => (
          <PiiOccurrenceRow key={item.entity_id} messageId={messageId} item={item} canReveal={canReveal} />
        ))}
      </div>
    </div>
  )
}

function PiiOccurrenceRow({
  messageId, item, canReveal,
}: {
  messageId: string
  item: PiiOccurrenceSummary
  canReveal: boolean
}) {
  const [rawValue, setRawValue] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Auto-hide timer — re-masks on its own; also cleared on unmount so no
  // stray timer holds a reference to a raw value after this row is gone.
  useEffect(() => {
    if (rawValue === null) return
    const timer = window.setTimeout(() => setRawValue(null), AUTO_HIDE_MS)
    return () => window.clearTimeout(timer)
  }, [rawValue])

  async function doReveal() {
    setConfirming(false)
    setLoading(true)
    setError(null)
    try {
      const result = await revealPiiOccurrence(messageId, item.entity_id)
      setRawValue(result.raw_value)
    } catch (err) {
      setError(getApiError(err, "Couldn't retrieve the original value.").message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center gap-3 border-b border-neutral-100 px-3 py-2 text-xs last:border-b-0">
      <span className="w-24 shrink-0 font-mono font-semibold text-ink">{item.entity_type}</span>
      <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-neutral-400">{item.direction}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-neutral-600">
        {rawValue ?? item.sanitized_value}
      </span>
      {rawValue !== null && (
        <span className="shrink-0 text-[10px] font-medium text-amber-700">original — auto-hides shortly</span>
      )}
      {canReveal && rawValue === null && (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={loading}
          className="flex shrink-0 items-center gap-1 rounded border border-neutral-300 px-2 py-0.5 text-[11px] font-medium text-neutral-600 transition-colors hover:border-accent-300 hover:text-accent-700 disabled:opacity-50"
        >
          <Eye className="h-3 w-3" /> {loading ? 'Loading…' : 'View Original'}
        </button>
      )}
      {rawValue !== null && (
        <button
          type="button"
          onClick={() => setRawValue(null)}
          className="shrink-0 text-[11px] font-medium text-neutral-400 hover:text-neutral-700"
        >
          Hide
        </button>
      )}
      {error && <span className="shrink-0 text-[11px] text-red-600">{error}</span>}
      {confirming && (
        <RevealConfirmDialog onCancel={() => setConfirming(false)} onConfirm={doReveal} entityType={item.entity_type} />
      )}
    </div>
  )
}

function RevealConfirmDialog({
  onCancel, onConfirm, entityType,
}: {
  onCancel: () => void
  onConfirm: () => void
  entityType: string
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onCancel])

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <button type="button" aria-label="Cancel" onClick={onCancel} className="animate-fade-in absolute inset-0 bg-neutral-900/30" />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label="Confirm viewing original PII"
        className="animate-fade-slide-up relative w-full max-w-sm rounded-lg border border-neutral-200 bg-surface p-5 shadow-xl"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-amber-600" />
            <h3 className="text-sm font-semibold text-ink">View original {entityType}?</h3>
          </div>
          <button type="button" onClick={onCancel} aria-label="Close" className="text-neutral-400 hover:text-ink">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-4 text-xs leading-relaxed text-neutral-600">
          Original PII is sensitive information. Your access will be recorded in the security audit log.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700"
          >
            View Original
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
