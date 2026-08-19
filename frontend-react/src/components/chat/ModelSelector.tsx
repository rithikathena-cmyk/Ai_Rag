import { useEffect, useState } from 'react'
import { Check, ChevronDown, Sparkles, Zap, Brain } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { ModelTier } from '@/types/chat'

const TIER_ORDER: ModelTier[] = ['haiku', 'sonnet', 'opus']

// `opus` is a permission tier name from the RBAC policy (backend/config/
// llm_rbac.yaml), not a promise of which model answers — this deployment's
// tier table (backend/config/models.yaml) resolves it to Sonnet, the ceiling
// model here. Labeled accordingly so the picker never claims a model it
// isn't actually calling.
const TIER_INFO: Record<ModelTier, { name: string; description: string; icon: typeof Zap }> = {
  haiku: { name: 'Claude Haiku', description: 'Fast responses', icon: Zap },
  sonnet: { name: 'Claude Sonnet', description: 'Balanced reasoning', icon: Sparkles },
  opus: { name: 'Claude Sonnet (priority)', description: 'Highest-priority routing', icon: Brain },
}

export function ModelSelector({
  value,
  onChange,
  allowedTiers,
}: {
  value: ModelTier
  onChange: (tier: ModelTier) => void
  allowedTiers: string[]
}) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open])

  const options = TIER_ORDER.filter((t) => allowedTiers.includes(t))
  const current = TIER_INFO[value] ?? TIER_INFO.haiku
  const CurrentIcon = current.icon

  if (options.length <= 1) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-neutral-500">
        <CurrentIcon className="h-3.5 w-3.5" />
        {current.name}
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative z-20 flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-surface px-2.5 py-1.5 text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <CurrentIcon className="h-3.5 w-3.5 text-accent-600" />
        {current.name}
        <ChevronDown className={cn('h-3 w-3 text-neutral-400 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close model selector"
            className="fixed inset-0 z-10 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            role="listbox"
            className="animate-fade-slide-up absolute bottom-full z-20 mb-2 w-64 overflow-hidden rounded-xl border border-neutral-200 bg-surface py-1 shadow-lg"
          >
            {options.map((tier) => {
              const info = TIER_INFO[tier]
              const Icon = info.icon
              const selected = tier === value
              return (
                <button
                  key={tier}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    onChange(tier)
                    setOpen(false)
                  }}
                  className={cn(
                    'flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors hover:bg-neutral-50',
                    selected && 'bg-accent-50',
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0 text-accent-600" />
                  <span className="flex-1">
                    <span className="block font-medium text-ink">{info.name}</span>
                    <span className="block text-xs text-neutral-500">{info.description}</span>
                  </span>
                  {selected && <Check className="h-4 w-4 shrink-0 text-accent-600" />}
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
