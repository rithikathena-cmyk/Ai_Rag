import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { FileText, X } from 'lucide-react'
import type { ChatSource } from '@/types/chat'

export function SourceModal({ source, onClose }: { source: ChatSource | null; onClose: () => void }) {
  useEffect(() => {
    if (!source) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [source, onClose])

  if (!source) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close source panel"
        onClick={onClose}
        className="animate-fade-in absolute inset-0 bg-neutral-900/20"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Source ${source.document_filename ?? source.document_id}`}
        className="animate-slide-in-right relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-neutral-200 bg-surface shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-neutral-200 p-5">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
              <FileText className="h-4 w-4" />
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-accent-600">Source [{source.index}]</p>
              <h3 className="text-sm font-semibold text-ink">{source.document_filename ?? source.document_id}</h3>
              <p className="mt-0.5 text-xs text-neutral-400">Chunk #{source.chunk_index}</p>
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

        <div className="space-y-4 p-5">
          {(source.document_department || source.document_type || source.security_classification) && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-neutral-200 p-3 text-xs">
              {source.document_department && (
                <MetaRow label="Department" value={source.document_department} />
              )}
              {source.document_type && <MetaRow label="Document type" value={source.document_type} />}
              {source.security_classification && (
                <MetaRow label="Security level" value={source.security_classification} />
              )}
            </dl>
          )}

          <div className="whitespace-pre-wrap rounded-lg bg-neutral-50 p-4 text-sm leading-relaxed text-neutral-700">
            {source.text}
          </div>

          <p className="text-xs text-neutral-400">
            This excerpt was retrieved from the knowledge base and used to help generate the response.
          </p>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-neutral-400">{label}</dt>
      <dd className="truncate font-medium capitalize text-ink">{value}</dd>
    </div>
  )
}
