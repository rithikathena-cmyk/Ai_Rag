import { cn } from '@/lib/cn'
import type { ActivityStatus, ChecksSummary } from '@/lib/guardrails'

const SCORE_TONE: Record<'good' | 'warn' | 'bad', { text: string; bar: string }> = {
  good: { text: 'text-emerald-600', bar: 'bg-emerald-500' },
  warn: { text: 'text-amber-600', bar: 'bg-amber-500' },
  bad: { text: 'text-red-600', bar: 'bg-red-500' },
}

function scoreTone(status: ActivityStatus): 'good' | 'warn' | 'bad' {
  if (status === 'PASSED') return 'good'
  if (status === 'BLOCKED') return 'bad'
  return 'warn'
}

// Shared by the chat Security & Activity panel and the Traces page's
// expanded row detail — same summary shape (lib/guardrails.ts's
// summarizeChecks), same "color reflects the worst outcome, not just the
// ratio" rule: a request that's 90% passed but blocked on one check shows
// red, not a falsely-reassuring mostly-green bar.
export function ChecksScoreBar({ summary, className }: { summary: ChecksSummary; className?: string }) {
  const tone = SCORE_TONE[scoreTone(summary.worstStatus)]
  return (
    <div className={className}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-neutral-500">Checks passed</span>
        <span className={cn('font-semibold tabular-nums', tone.text)}>
          {summary.passed} / {summary.total} · {summary.percent}%
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
        <div
          className={cn('h-full rounded-full transition-all duration-300', tone.bar)}
          style={{ width: `${summary.percent}%` }}
        />
      </div>
    </div>
  )
}
