import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { DollarSign, Gauge, ServerCrash, Shield } from 'lucide-react'
import { getGatewayUsage, getGuardrailAnalytics, getMetrics } from '@/api/metrics'
import { getApiError } from '@/lib/apiError'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { Tabs } from '@/components/ui/Tabs'
import { useCountUp } from '@/hooks/useCountUp'

const TAB_OPTIONS = [
  { value: 'performance', label: 'Latency & Tokens' },
  { value: 'gateway', label: 'Gateway Cost' },
  { value: 'guardrails', label: 'Guardrails' },
]

const REFRESH_MS = 30_000

export function MetricsPage() {
  const [tab, setTab] = useState('performance')

  return (
    <div>
      <PageHeader
        title="Metrics"
        description="Usage and performance metrics"
        actions={<LiveIndicator />}
      />
      <Tabs options={TAB_OPTIONS} value={tab} onChange={setTab} />
      <div key={tab} className="animate-fade-slide-up">
        {tab === 'performance' && <PerformanceTab />}
        {tab === 'gateway' && <GatewayTab />}
        {tab === 'guardrails' && <GuardrailsTab />}
      </div>
    </div>
  )
}

function LiveIndicator() {
  return (
    <div className="flex items-center gap-1.5 text-xs text-neutral-400">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
      </span>
      Live · refreshes every 30s
    </div>
  )
}

function StatCard({ icon: Icon, label, value, index = 0 }: { icon: typeof Gauge; label: string; value: number; index?: number }) {
  const animated = useCountUp(value)
  return (
    <Card className="animate-fade-slide-up" style={{ animationDelay: `${index * 50}ms` }}>
      <CardBody className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-lg font-semibold tabular-nums text-ink">{animated.toLocaleString()}</p>
          <p className="text-xs text-neutral-500">{label}</p>
        </div>
      </CardBody>
    </Card>
  )
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <StateMessage
      icon={ServerCrash}
      tone="error"
      title="Couldn't load metrics"
      description={getApiError(error, 'Something went wrong.').message}
      action={
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      }
    />
  )
}

function PerformanceTab() {
  const query = useQuery({ queryKey: ['metrics', 'performance'], queryFn: getMetrics, refetchInterval: REFRESH_MS })

  if (query.isLoading) return <SkeletonRows rows={6} cols={4} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const data = query.data!
  const maxAvg = Math.max(1, ...data.latency_summary.map((s) => s.avg_ms))

  return (
    <div className="space-y-6 p-6">
      <div>
        <h3 className="mb-3 text-sm font-semibold text-ink">Endpoint latency</h3>
        {data.latency_summary.length === 0 ? (
          <p className="text-sm text-neutral-400">No latency samples recorded yet.</p>
        ) : (
          <div className="space-y-1.5">
            {data.latency_summary.map((s, i) => (
              <div
                key={s.endpoint}
                className="animate-fade-slide-up grid grid-cols-[1fr_auto] items-center gap-3 rounded-lg px-2 py-1.5 text-sm transition-colors duration-150 hover:bg-neutral-50"
                style={{ animationDelay: `${i * 25}ms` }}
              >
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-medium text-ink">{s.endpoint}</span>
                    <span className="tabular-nums text-neutral-500">
                      {s.count}× · avg {s.avg_ms.toFixed(0)}ms · p95 {s.p95_ms.toFixed(0)}ms
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
                    <div
                      className="h-full rounded-full bg-accent-500 transition-all duration-700 ease-out"
                      style={{ width: `${Math.max(2, (s.avg_ms / maxAvg) * 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <h3 className="mb-3 text-sm font-semibold text-ink">Token usage by source/model</h3>
        {data.token_usage_summary.length === 0 ? (
          <p className="text-sm text-neutral-400">No token usage recorded yet.</p>
        ) : (
          <Table
            columns={['Source', 'Model', 'Calls', 'Input tokens', 'Output tokens']}
            rows={data.token_usage_summary.map((s) => [
              s.source,
              s.model,
              s.call_count.toLocaleString(),
              s.total_input_tokens.toLocaleString(),
              s.total_output_tokens.toLocaleString(),
            ])}
          />
        )}
      </div>
    </div>
  )
}

function GatewayTab() {
  const query = useQuery({ queryKey: ['metrics', 'gateway'], queryFn: () => getGatewayUsage(), refetchInterval: REFRESH_MS })

  if (query.isLoading) return <SkeletonRows rows={5} cols={4} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const data = query.data!
  const maxCost = Math.max(0.0001, ...data.summary.map((s) => s.total_cost_usd))

  return (
    <div className="space-y-6 p-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="animate-fade-slide-up">
          <CardBody className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
              <DollarSign className="h-4 w-4" />
            </div>
            <div>
              <p className="text-lg font-semibold tabular-nums text-ink">${data.total_cost_usd.toFixed(2)}</p>
              <p className="text-xs text-neutral-500">Total cost</p>
            </div>
          </CardBody>
        </Card>
        <StatCard index={1} icon={Gauge} label="Calls tracked" value={data.samples.length} />
        <StatCard index={2} icon={Gauge} label="Agent/model combos" value={data.summary.length} />
      </div>
      {data.summary.length === 0 ? (
        <p className="text-sm text-neutral-400">No gateway usage recorded yet.</p>
      ) : (
        <div className="space-y-1.5">
          {data.summary.map((s, i) => (
            <div
              key={`${s.agent_name}-${s.model}-${s.tier}`}
              className="animate-fade-slide-up rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-neutral-50"
              style={{ animationDelay: `${i * 25}ms` }}
            >
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="font-medium text-ink">
                  {s.agent_name} <span className="text-neutral-400">·</span> {s.model}{' '}
                  <Badge tone="neutral">{s.tier}</Badge>
                </span>
                <span className="tabular-nums text-neutral-600">
                  ${s.total_cost_usd.toFixed(4)} · {s.call_count.toLocaleString()} calls · avg {s.avg_latency_ms.toFixed(0)}ms
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all duration-700 ease-out"
                  style={{ width: `${Math.max(2, (s.total_cost_usd / maxCost) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function GuardrailsTab() {
  const query = useQuery({ queryKey: ['metrics', 'guardrails'], queryFn: getGuardrailAnalytics, refetchInterval: REFRESH_MS })

  if (query.isLoading) return <SkeletonRows rows={5} cols={4} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  const data = query.data!

  return (
    <div className="space-y-6 p-6">
      {data.summary.length === 0 ? (
        <p className="text-sm text-neutral-400">No guardrail events recorded yet.</p>
      ) : (
        <div>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink">
            <Shield className="h-4 w-4" /> Checks by direction
          </h3>
          <div className="space-y-1.5">
            {data.summary.map((s, i) => {
              const total = s.pass_count + s.redact_count + s.block_count
              return (
                <div
                  key={`${s.direction}-${s.check}`}
                  className="animate-fade-slide-up rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-neutral-50"
                  style={{ animationDelay: `${i * 25}ms` }}
                >
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span className="font-medium text-ink">
                      {s.direction} <span className="text-neutral-400">·</span> {s.check}
                    </span>
                    <span className="tabular-nums text-neutral-600">
                      {s.pass_count} pass · {s.redact_count} redact · {s.block_count} block
                    </span>
                  </div>
                  {total > 0 && (
                    <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
                      <div className="h-full bg-emerald-500 transition-all duration-700" style={{ width: `${(s.pass_count / total) * 100}%` }} />
                      <div className="h-full bg-amber-500 transition-all duration-700" style={{ width: `${(s.redact_count / total) * 100}%` }} />
                      <div className="h-full bg-red-500 transition-all duration-700" style={{ width: `${(s.block_count / total) * 100}%` }} />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {data.events.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-ink">Recent events</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="pb-2 font-medium">Direction</th>
                  <th className="pb-2 font-medium">Check</th>
                  <th className="pb-2 font-medium">Action</th>
                  <th className="pb-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {data.events.slice(-30).reverse().map((e, i) => (
                  <tr
                    key={i}
                    className="animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50"
                    style={{ animationDelay: `${i * 20}ms` }}
                  >
                    <td className="py-2 pr-4 text-neutral-600">{e.direction}</td>
                    <td className="py-2 pr-4 text-neutral-600">{e.check}</td>
                    <td className="py-2 pr-4">
                      <Badge tone={e.action === 'block' ? 'red' : e.action === 'redact' ? 'amber' : 'green'}>
                        {e.action}
                      </Badge>
                    </td>
                    <td className="py-2 pr-4 text-neutral-500">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Table({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-neutral-200 text-neutral-500">
            {columns.map((c) => (
              <th key={c} className="pb-2 pr-4 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50"
              style={{ animationDelay: `${i * 25}ms` }}
            >
              {row.map((cell, j) => (
                <td key={j} className="py-2 pr-4 tabular-nums text-neutral-600 first:font-medium first:text-ink">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
