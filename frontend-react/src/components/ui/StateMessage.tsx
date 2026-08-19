import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/cn'

export function StateMessage({
  icon: Icon,
  title,
  description,
  tone = 'neutral',
  action,
  className,
}: {
  icon: LucideIcon
  title: string
  description?: string
  tone?: 'neutral' | 'error'
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-16 text-center', className)}>
      <div
        className={cn(
          'flex h-11 w-11 items-center justify-center rounded-full',
          tone === 'error' ? 'bg-red-100 text-red-600' : 'bg-neutral-100 text-neutral-500',
        )}
      >
        <Icon className="h-5 w-5" />
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && <p className="max-w-sm text-sm text-neutral-500">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
