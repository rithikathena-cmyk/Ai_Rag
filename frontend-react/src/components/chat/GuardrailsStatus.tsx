import { useState } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { isBlockedResponse } from '@/lib/guardrails'
import { SecurityActivityPanel } from '@/components/chat/SecurityActivityPanel'
import type { ChatTraceStep } from '@/types/chat'

export function GuardrailsStatus({ trace, responseTimeMs }: { trace: ChatTraceStep[]; responseTimeMs?: number }) {
  const [open, setOpen] = useState(false)
  const blocked = isBlockedResponse(trace)
  const duration = responseTimeMs != null ? `${(responseTimeMs / 1000).toFixed(2)}s` : null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex items-center gap-1 text-xs text-neutral-400 transition-colors hover:text-neutral-700"
      >
        {blocked ? <ShieldAlert className="h-3 w-3 text-red-500" /> : <ShieldCheck className="h-3 w-3 text-emerald-500" />}
        Security &amp; Activity
        {!blocked && duration ? ` · ${duration}` : ''}
      </button>
      <SecurityActivityPanel trace={trace} responseTimeMs={responseTimeMs} open={open} onClose={() => setOpen(false)} />
    </>
  )
}
