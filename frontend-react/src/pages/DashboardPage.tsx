import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, FileText, MessagesSquare, RefreshCw, Shield, ShieldCheck, Zap } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'
import { listDocuments } from '@/api/documents'
import { listConversations } from '@/api/chat'
import { listTraces } from '@/api/traces'
import { getHealth } from '@/api/health'
import { getMyUsage } from '@/api/users'
import { getGuardrailAnalytics } from '@/api/metrics'
import { PageHeader } from '@/components/layout/PageHeader'
import { Card, CardBody } from '@/components/ui/Card'
import { isNavItemVisible, NAV_ITEMS } from '@/components/layout/nav'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { TraceListItem } from '@/types/traces'

function getGreeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function DashboardPage() {
  const { user, capabilities, hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const canViewAnalytics = hasPermission('VIEW_ANALYTICS')

  const documentsQuery = useQuery({
    queryKey: ['documents', 'count'],
    queryFn: () => listDocuments(1, 0),
    enabled: hasPermission('VIEW_DOCUMENTS'),
  })
  const conversationsQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(),
  })
  // user_id is always honored server-side, even for CEO/Admin (whose
  // broader role would otherwise see org-wide traces) — see
  // routers/traces.py: a caller-supplied user_id narrows a privileged
  // caller's own broad visibility down to just that one user, while a
  // non-privileged caller is hard-scoped to themselves regardless. Passing
  // the current user's own id here is what keeps "Recent activity" personal
  // to whoever's looking at their own dashboard, not a role-dependent view.
  const recentTracesQuery = useQuery({
    queryKey: ['dashboard', 'traces', user?.id],
    queryFn: () => listTraces({ user_id: user!.id, limit: 20 }),
    enabled: Boolean(user?.id),
  })
  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    retry: 0,
    refetchInterval: 60_000,
  })
  const usageQuery = useQuery({
    queryKey: ['users', 'me', 'usage'],
    queryFn: getMyUsage,
  })
  const guardrailsQuery = useQuery({
    queryKey: ['dashboard', 'guardrails'],
    queryFn: getGuardrailAnalytics,
    enabled: canViewAnalytics,
    refetchInterval: 60_000,
  })

  function handleRefresh() {
    setRefreshing(true)
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    void queryClient.invalidateQueries({ queryKey: ['health'] })
    void queryClient.invalidateQueries({ queryKey: ['users', 'me', 'usage'] })
    void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    window.setTimeout(() => setRefreshing(false), 700)
  }

  const shortcuts = NAV_ITEMS.filter((item) => item.to !== '/dashboard' && isNavItemVisible(item, hasPermission, user?.role))

  // Traces are already ordered most-recent-first server-side; dedupe to one
  // (the most recent) entry per conversation so a conversation with several
  // recent messages doesn't crowd out other conversations in this list —
  // fetched 20 rather than 5 for exactly that reason, since several of the
  // top 5 raw rows can easily belong to the same conversation.
  const recentQuestions = useMemo(() => {
    const seen = new Set<string>()
    const items: TraceListItem[] = []
    for (const item of recentTracesQuery.data?.items ?? []) {
      if (seen.has(item.conversation_id)) continue
      seen.add(item.conversation_id)
      items.push(item)
      if (items.length >= 5) break
    }
    return items
  }, [recentTracesQuery.data])

  const systemStatus = healthQuery.isError ? 'unavailable' : healthQuery.isLoading ? 'checking' : 'operational'

  return (
    <div>
      <PageHeader
        title={`${getGreeting()}${user?.display_name ? `, ${user.display_name}` : ''}`}
        description={capabilities?.display_name ?? user?.role}
        actions={
          <button
            type="button"
            onClick={handleRefresh}
            aria-label="Refresh dashboard"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-neutral-300 bg-surface text-neutral-500 transition-colors duration-150 hover:bg-neutral-100 hover:text-ink"
          >
            <RefreshCw className={cn('h-4 w-4 transition-transform duration-500', refreshing && 'animate-spin')} />
          </button>
        }
      />

      <div className="grid grid-cols-1 gap-4 p-6 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          index={0}
          icon={FileText}
          label="Documents"
          value={documentsQuery.data?.total}
          loading={hasPermission('VIEW_DOCUMENTS') && documentsQuery.isLoading}
          to={hasPermission('VIEW_DOCUMENTS') ? '/documents' : undefined}
        />
        <StatCard
          index={1}
          icon={MessagesSquare}
          label="Conversations"
          value={conversationsQuery.data?.total}
          loading={conversationsQuery.isLoading}
          to="/"
        />
        <StatCard
          index={2}
          icon={ShieldCheck}
          label="Role"
          value={capabilities?.display_name ?? user?.role ?? '—'}
        />
        <StatCard
          index={3}
          icon={Activity}
          label="System status"
          value={
            systemStatus === 'operational' ? 'Operational' : systemStatus === 'checking' ? 'Checking...' : 'Unavailable'
          }
          tone={systemStatus === 'operational' ? 'good' : systemStatus === 'checking' ? 'neutral' : 'bad'}
          pulse={systemStatus === 'operational'}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 px-6 pb-4 lg:grid-cols-2">
        <UsageCard usageQuery={usageQuery} />
        {canViewAnalytics && <GuardrailsCard guardrailsQuery={guardrailsQuery} />}
      </div>

      <div className="grid grid-cols-1 gap-4 px-6 pb-6 lg:grid-cols-2">
        {shortcuts.length > 0 && (
          <div>
            <h2 className="mb-3 text-sm font-semibold text-ink">Quick access</h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {shortcuts.map(({ to, label, icon: Icon }, i) => (
                <Link key={to} to={to} className="animate-fade-slide-up" style={{ animationDelay: `${i * 40}ms` }}>
                  <Card className="group transition-all duration-150 hover:-translate-y-0.5 hover:border-accent-300 hover:shadow-md">
                    <CardBody className="flex items-center gap-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-100 text-accent-700 transition-transform duration-150 group-hover:scale-110">
                        <Icon className="h-4 w-4" />
                      </div>
                      <span className="text-sm font-medium text-ink">{label}</span>
                    </CardBody>
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        )}

        <div>
          <h2 className="mb-3 text-sm font-semibold text-ink">Recent activity</h2>
          <Card>
            {recentTracesQuery.isLoading ? (
              <CardBody>
                <div className="space-y-2">
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="h-5 animate-pulse rounded bg-neutral-100" />
                  ))}
                </div>
              </CardBody>
            ) : recentQuestions.length === 0 ? (
              <CardBody>
                <p className="text-sm text-neutral-400">No questions asked yet</p>
              </CardBody>
            ) : (
              <ul className="divide-y divide-neutral-100">
                {recentQuestions.map((item, i) => (
                  <li key={item.message_id} className="animate-fade-slide-up" style={{ animationDelay: `${i * 40}ms` }}>
                    <Link
                      to={`/?conversation=${item.conversation_id}`}
                      className="group flex items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-neutral-50"
                    >
                      <MessagesSquare className="h-3.5 w-3.5 shrink-0 text-neutral-300 transition-colors duration-150 group-hover:text-accent-500" />
                      <span className="min-w-0 flex-1 truncate text-ink">{item.question ?? 'New conversation'}</span>
                      <span className="shrink-0 text-xs text-neutral-400">
                        {new Date(item.created_at).toLocaleDateString()}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  to,
  tone = 'neutral',
  loading = false,
  pulse = false,
  index = 0,
}: {
  icon: typeof FileText
  label: string
  value: string | number | undefined
  to?: string
  tone?: 'good' | 'bad' | 'neutral'
  loading?: boolean
  pulse?: boolean
  index?: number
}) {
  const numericTarget = typeof value === 'number' ? value : null
  const animated = useCountUp(numericTarget)
  const display = numericTarget != null ? animated.toLocaleString() : (value ?? '—')

  const content = (
    <Card
      className={cn(
        'h-full animate-fade-slide-up transition-all duration-150',
        to && 'hover:-translate-y-0.5 hover:border-accent-300 hover:shadow-md',
      )}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      <CardBody className="flex items-center gap-3">
        <div
          className={cn(
            'relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
            tone === 'good' && 'bg-emerald-100 text-emerald-700',
            tone === 'bad' && 'bg-red-100 text-red-700',
            tone === 'neutral' && 'bg-accent-100 text-accent-700',
          )}
        >
          <Icon className="h-4 w-4" />
          {pulse && (
            <span className="absolute -right-0.5 -top-0.5 flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
          )}
        </div>
        <div className="min-w-0">
          {loading ? (
            <div className="h-6 w-14 animate-pulse rounded bg-neutral-200" />
          ) : (
            <p className="truncate text-lg font-semibold tabular-nums text-ink">{display}</p>
          )}
          <p className="text-xs text-neutral-500">{label}</p>
        </div>
      </CardBody>
    </Card>
  )

  return to ? <Link to={to}>{content}</Link> : content
}

function UsageCard({ usageQuery }: { usageQuery: ReturnType<typeof useQuery<Awaited<ReturnType<typeof getMyUsage>>>> }) {
  return (
    <Card className="animate-fade-slide-up" style={{ animationDelay: '240ms' }}>
      <CardBody>
        <div className="mb-3 flex items-center gap-2">
          <Zap className="h-4 w-4 text-accent-600" />
          <h3 className="text-sm font-semibold text-ink">Your usage</h3>
        </div>
        {usageQuery.isLoading ? (
          <div className="space-y-3">
            {[0, 1].map((i) => (
              <div key={i} className="h-8 animate-pulse rounded bg-neutral-100" />
            ))}
          </div>
        ) : usageQuery.data ? (
          <div className="space-y-3">
            <UsageBar label="Daily tokens" used={usageQuery.data.daily_tokens_used} limit={usageQuery.data.daily_tokens_limit} />
            <UsageBar
              label="Monthly tokens"
              used={usageQuery.data.monthly_tokens_used}
              limit={usageQuery.data.monthly_tokens_limit}
            />
          </div>
        ) : (
          <p className="text-sm text-neutral-400">Usage isn't available right now.</p>
        )}
      </CardBody>
    </Card>
  )
}

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : null
  const tone = pct != null && pct > 90 ? 'bg-red-500' : pct != null && pct > 70 ? 'bg-amber-500' : 'bg-accent-500'

  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-neutral-500">{label}</span>
        <span className="font-medium tabular-nums text-ink">
          {used.toLocaleString()}
          {limit != null ? ` / ${limit.toLocaleString()}` : ' used · Unlimited'}
        </span>
      </div>
      {pct != null && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
          <div
            className={cn('h-full rounded-full transition-all duration-700 ease-out', tone)}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  )
}

function GuardrailsCard({
  guardrailsQuery,
}: {
  guardrailsQuery: ReturnType<typeof useQuery<Awaited<ReturnType<typeof getGuardrailAnalytics>>>>
}) {
  const totals = useMemo(() => {
    const summary = guardrailsQuery.data?.summary ?? []
    return summary.reduce(
      (acc, s) => ({
        pass: acc.pass + s.pass_count,
        redact: acc.redact + s.redact_count,
        block: acc.block + s.block_count,
      }),
      { pass: 0, redact: 0, block: 0 },
    )
  }, [guardrailsQuery.data])

  const pass = useCountUp(guardrailsQuery.data ? totals.pass : null)
  const redact = useCountUp(guardrailsQuery.data ? totals.redact : null)
  const block = useCountUp(guardrailsQuery.data ? totals.block : null)
  const total = totals.pass + totals.redact + totals.block

  return (
    <Card className="animate-fade-slide-up" style={{ animationDelay: '300ms' }}>
      <CardBody>
        <div className="mb-3 flex items-center gap-2">
          <Shield className="h-4 w-4 text-accent-600" />
          <h3 className="text-sm font-semibold text-ink">Guardrails</h3>
        </div>
        {guardrailsQuery.isLoading ? (
          <div className="h-8 animate-pulse rounded bg-neutral-100" />
        ) : (
          <>
            {total > 0 && (
              <div className="mb-3 flex h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
                <div className="h-full bg-emerald-500 transition-all duration-700" style={{ width: `${(totals.pass / total) * 100}%` }} />
                <div className="h-full bg-amber-500 transition-all duration-700" style={{ width: `${(totals.redact / total) * 100}%` }} />
                <div className="h-full bg-red-500 transition-all duration-700" style={{ width: `${(totals.block / total) * 100}%` }} />
              </div>
            )}
            <div className="grid grid-cols-3 gap-2 text-center">
              <GuardrailStat label="Passed" value={pass} tone="text-emerald-600" />
              <GuardrailStat label="Redacted" value={redact} tone="text-amber-600" />
              <GuardrailStat label="Blocked" value={block} tone="text-red-600" />
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

function GuardrailStat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div>
      <p className={cn('text-lg font-semibold tabular-nums', tone)}>{value.toLocaleString()}</p>
      <p className="text-xs text-neutral-500">{label}</p>
    </div>
  )
}
