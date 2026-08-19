import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Route as RouteIcon, ServerCrash } from 'lucide-react'
import { listTraces } from '@/api/traces'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { buildActivityTimeline, extractCheckScore, pipelineCompletionPercent, summarizeChecks } from '@/lib/guardrails'
import { PageHeader } from '@/components/layout/PageHeader'
import { PiiOccurrencesPanel } from '@/components/pii/PiiOccurrencesPanel'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ChecksScoreBar } from '@/components/ui/ChecksScoreBar'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { cn } from '@/lib/cn'
import type { TraceListItem } from '@/types/traces'

const ROLE_FILTERS = [
  { value: undefined, label: 'All roles' },
  { value: 'user', label: 'Employee' },
  { value: 'hr', label: 'HR' },
  { value: 'project_manager', label: 'Project Manager' },
  { value: 'ceo', label: 'CEO' },
  { value: 'admin', label: 'Admin' },
] as const

const STATUS_FILTERS = [
  { value: undefined, label: 'All' },
  { value: false, label: 'Allowed' },
  { value: true, label: 'Blocked' },
] as const

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function truncate(text: string | null, max: number): string {
  if (!text) return '—'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function ProgressBar({ percent, blocked }: { percent: number; blocked: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-neutral-100">
        <div
          className={cn('h-full rounded-full transition-all duration-300', blocked ? 'bg-red-500' : 'bg-emerald-500')}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="w-9 shrink-0 tabular-nums text-xs text-neutral-500">{percent}%</span>
    </div>
  )
}

export function TracesPage() {
  const { hasPermission } = useAuth()
  const canSeeAllUsers = hasPermission('VIEW_AUDIT_LOGS')
  const [role, setRole] = useState<string | undefined>(undefined)
  const [blocked, setBlocked] = useState<boolean | undefined>(undefined)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [page, setPage] = useState(0)

  // A smaller page, paged with Prev/Next, instead of one long 100-row dump —
  // any filter change (including a fresh mount of this component) starts
  // back at page 0 via the queryKey below rather than stranding the user on
  // an out-of-range page for the new filter set.
  const PAGE_SIZE = 20
  const query = useQuery({
    queryKey: ['traces', role, blocked, dateFrom, dateTo, page],
    queryFn: () =>
      listTraces({
        role, blocked, date_from: dateFrom || undefined, date_to: dateTo ? `${dateTo}T23:59:59` : undefined,
        limit: PAGE_SIZE, offset: page * PAGE_SIZE,
      }),
  })

  function updateFilter<T>(setter: (value: T) => void, value: T) {
    setter(value)
    setPage(0)
  }

  return (
    <div>
      <PageHeader
        title="Traces"
        description={
          canSeeAllUsers
            ? "Every chat request's guardrail pipeline, with the full unsanitized check detail (score/threshold included)"
            : 'Your chat requests’ guardrail pipeline, with the full unsanitized check detail (score/threshold included)'
        }
      />
      <div className="p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="flex gap-1.5">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.label}
                type="button"
                onClick={() => updateFilter(setBlocked, f.value)}
                className={cn(
                  'rounded-full px-3 py-1 text-xs font-medium transition-colors duration-150',
                  blocked === f.value ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          {canSeeAllUsers && (
            <select
              value={role ?? ''}
              onChange={(e) => updateFilter(setRole, e.target.value || undefined)}
              className="rounded-md border border-neutral-200 bg-white px-2.5 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
            >
              {ROLE_FILTERS.map((f) => (
                <option key={f.label} value={f.value ?? ''}>
                  {f.label}
                </option>
              ))}
            </select>
          )}
          <div className="flex items-center gap-1.5 text-xs text-neutral-500">
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => updateFilter(setDateFrom, e.target.value)}
              className="rounded-md border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
            />
            <span>–</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => updateFilter(setDateTo, e.target.value)}
              className="rounded-md border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
        </div>

        {query.isLoading ? (
          <SkeletonRows rows={8} cols={6} />
        ) : query.isError ? (
          <StateMessage
            icon={ServerCrash}
            tone="error"
            title="Couldn't load traces"
            description={getApiError(query.error, 'Something went wrong.').message}
            action={
              <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
                Try again
              </Button>
            }
          />
        ) : query.data!.items.length === 0 ? (
          <StateMessage icon={RouteIcon} title="No traces recorded yet" />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-neutral-200 text-neutral-500">
                    <th className="pb-2 pr-4 font-medium" />
                    <th className="pb-2 pr-4 font-medium">When</th>
                    {canSeeAllUsers && <th className="pb-2 pr-4 font-medium">User</th>}
                    <th className="pb-2 pr-4 font-medium">Question</th>
                    <th className="pb-2 pr-4 font-medium">Progress</th>
                    <th className="pb-2 pr-4 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data!.items.map((item, i) => (
                    <TraceRow
                      key={item.message_id}
                      item={item}
                      index={i}
                      expanded={expandedId === item.message_id}
                      onToggle={() => setExpandedId((cur) => (cur === item.message_id ? null : item.message_id))}
                      showUser={canSeeAllUsers}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex items-center justify-between text-xs text-neutral-400">
              <span>
                {query.data!.total === 0
                  ? 'No traces'
                  : `${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + query.data!.items.length} of ${query.data!.total.toLocaleString()}`}
              </span>
              <div className="flex gap-1.5">
                <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={(page + 1) * PAGE_SIZE >= query.data!.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function TraceRow({
  item,
  index,
  expanded,
  onToggle,
  showUser,
}: {
  item: TraceListItem
  index: number
  expanded: boolean
  onToggle: () => void
  showUser: boolean
}) {
  const timeline = buildActivityTimeline(item.trace, { sanitize: false })
  const percent = pipelineCompletionPercent(item.trace)
  const checksSummary = summarizeChecks(timeline)
  const isBlocked = timeline.finalStatus === 'BLOCKED'
  const colCount = showUser ? 6 : 5

  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          'animate-fade-slide-up cursor-pointer border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50',
          expanded && 'bg-accent-50/60',
        )}
        style={{ animationDelay: `${Math.min(index, 20) * 25}ms` }}
      >
        <td className="py-2.5 pl-1">
          <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform duration-150', expanded && 'rotate-90')} />
        </td>
        <td className="py-2.5 pr-4 text-neutral-600">{formatDate(item.created_at)}</td>
        {showUser && (
          <td className="py-2.5 pr-4">
            <div className="font-medium text-ink">{item.user_email ?? (item.role ? 'Unattributed' : 'Unknown')}</div>
            {item.role && <div className="text-xs text-neutral-400">{item.role}{item.department ? ` · ${item.department}` : ''}</div>}
          </td>
        )}
        <td className="py-2.5 pr-4 max-w-xs text-neutral-600">{truncate(item.question, 60)}</td>
        <td className="py-2.5 pr-4">
          <ProgressBar percent={percent} blocked={isBlocked} />
        </td>
        <td className="py-2.5 pr-4">
          <Badge tone={isBlocked ? 'red' : 'green'}>{timeline.finalStatus}</Badge>
        </td>
      </tr>
      {expanded && (
        <tr className="animate-fade-slide-up border-b border-neutral-100 bg-neutral-50/60">
          <td colSpan={colCount} className="px-4 py-3">
            <div className="space-y-4">
              <ChecksScoreBar summary={checksSummary} className="rounded-lg border border-neutral-200 bg-surface p-3" />
              <PiiOccurrencesPanel messageId={item.message_id} />
              {timeline.sections.map((section) => (
                <div key={section.key} className="space-y-1.5">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-400">{section.title}</p>
                  <div className="grid grid-cols-1 gap-x-6 gap-y-2 lg:grid-cols-2">
                    {section.items.map((check) => {
                      const dotColor =
                        check.status === 'BLOCKED' ? 'bg-red-500' : check.status === 'PASSED' ? 'bg-emerald-500' : 'bg-amber-500'
                      const score = check.message ? extractCheckScore(check.message) : null
                      // Checks without a real underlying score (most of
                      // them — length, secrets, PII, citations, etc.) still
                      // get a bar for visual consistency, but it's a full
                      // STATUS bar, not a fabricated number: 100% filled,
                      // colored by outcome. Only the checks extractCheckScore
                      // actually finds a value for get a magnitude-scaled
                      // SCORE bar with a real decimal. SKIPPED checks never
                      // ran, so neither kind applies.
                      const showBar = check.status !== 'SKIPPED'
                      const barWidth = score != null ? score * 100 : 100
                      const barCaption = score != null ? 'Score' : 'Status'
                      const barLabel = score != null ? score.toFixed(2) : check.status.charAt(0) + check.status.slice(1).toLowerCase()
                      return (
                        <div key={check.key} className="flex items-start gap-2 text-xs">
                          <span className={cn('mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full', dotColor)} />
                          <span className="min-w-0 flex-1 text-neutral-600">
                            <span className="font-medium text-ink">{check.label}</span>
                            <span className="text-neutral-400"> · {check.status.charAt(0) + check.status.slice(1).toLowerCase()}</span>
                            {/* whitespace-pre-line: every check's message is one line except
                                scope_semantic_check/scope_semantic_mixed's per-clause breakdown
                                (raw view only — sanitize=false), which uses real newlines to
                                list each detected intent on its own line. */}
                            {check.message && <span className="block whitespace-pre-line text-neutral-500">{check.message}</span>}
                            {showBar && (
                              <span className="mt-1 flex items-center gap-1.5">
                                <span className="text-[10px] uppercase tracking-wide text-neutral-400">{barCaption}</span>
                                <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-neutral-100">
                                  <span className={cn('block h-full rounded-full', dotColor)} style={{ width: `${barWidth}%` }} />
                                </span>
                                <span className="tabular-nums font-medium text-neutral-500">{barLabel}</span>
                              </span>
                            )}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
              <div className="flex items-center gap-2 border-t border-neutral-200 pt-2 text-xs text-neutral-400">
                <span className="font-medium text-neutral-500">Message ID</span>
                <span>{item.message_id}</span>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
