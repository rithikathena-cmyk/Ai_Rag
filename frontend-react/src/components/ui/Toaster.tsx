import { useSyncExternalStore } from 'react'
import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { dismissToast, getToasts, subscribeToasts, type ToastTone } from '@/lib/toast'
import { cn } from '@/lib/cn'

const TONE_STYLES: Record<ToastTone, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  error: 'border-red-200 bg-red-50 text-red-800',
  info: 'border-neutral-200 bg-surface text-ink',
}

const TONE_ICON: Record<ToastTone, typeof Info> = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
}

export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, getToasts, getToasts)

  if (toasts.length === 0) return null

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => {
        const Icon = TONE_ICON[t.tone]
        return (
          <div
            key={t.id}
            role="status"
            className={cn(
              'pointer-events-auto flex animate-slide-in-right items-start gap-2 rounded-lg border px-3.5 py-3 text-sm shadow-md',
              TONE_STYLES[t.tone],
            )}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <p className="flex-1">{t.message}</p>
            <button
              type="button"
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 rounded p-0.5 text-current opacity-60 transition-opacity hover:opacity-100"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
