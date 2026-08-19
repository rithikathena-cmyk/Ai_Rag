import { cn } from '@/lib/cn'

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded bg-neutral-200', className)} />
}

export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-6">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4" style={{ animationDelay: `${i * 40}ms` }}>
          {Array.from({ length: cols }, (_, j) => (
            <Skeleton key={j} className={cn('h-4', j === 0 ? 'w-48' : 'w-20')} />
          ))}
        </div>
      ))}
    </div>
  )
}
