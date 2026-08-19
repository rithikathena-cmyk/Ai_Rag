import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, ClipboardCheck, Database, RefreshCw, ServerCrash, XCircle,
} from 'lucide-react'
import { decideApproval, getIndexConsistency, getModelAvailability, listApprovals, listCollections, setModelAvailability } from '@/api/admin'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { Tabs } from '@/components/ui/Tabs'
import { Toggle } from '@/components/ui/Toggle'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { ApprovalItem } from '@/types/admin'

const TAB_OPTIONS = [
  { value: 'approvals', label: 'Approvals' },
  { value: 'collections', label: 'Collections' },
  { value: 'consistency', label: 'Index consistency' },
  { value: 'availability', label: 'Model availability' },
]

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function AdminPage() {
  const [tab, setTab] = useState('approvals')

  return (
    <div>
      <PageHeader title="Admin" description="System settings and approvals" />
      <Tabs options={TAB_OPTIONS} value={tab} onChange={setTab} />
      <div key={tab} className="animate-fade-slide-up">
        {tab === 'approvals' && <ApprovalsTab />}
        {tab === 'collections' && <CollectionsTab />}
        {tab === 'consistency' && <ConsistencyTab />}
        {tab === 'availability' && <AvailabilityTab />}
      </div>
    </div>
  )
}

function ApprovalsTab() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('pending')
  const [reasonById, setReasonById] = useState<Record<string, string>>({})
  const [justDecidedId, setJustDecidedId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['approvals', status],
    queryFn: () => listApprovals(status),
  })
  const total = useCountUp(query.data?.total)

  const decideMutation = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: 'approved' | 'rejected' }) =>
      decideApproval(id, { decision, reason: reasonById[id] || undefined }),
    onSuccess: (_result, { id }) => {
      toast.success('Decision recorded')
      setJustDecidedId(id)
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['approvals'] }), 350)
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't record that decision.").message),
  })

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex gap-1.5">
          {['pending', 'approved', 'rejected'].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-medium capitalize transition-colors duration-150',
                status === s ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
              )}
            >
              {s}
            </button>
          ))}
        </div>
        {query.data && query.data.total > 0 && (
          <Badge tone="neutral" className="tabular-nums">
            {total.toLocaleString()} {query.data.total === 1 ? 'request' : 'requests'}
          </Badge>
        )}
      </div>

      {query.isLoading ? (
        <SkeletonRows rows={4} cols={3} />
      ) : query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load approvals"
          description={getApiError(query.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : query.data!.items.length === 0 ? (
        <StateMessage icon={ClipboardCheck} title={`No ${status} approvals`} />
      ) : (
        <div className="space-y-3">
          {query.data!.items.map((a: ApprovalItem, i: number) => (
            <Card
              key={a.id}
              className={cn(
                'animate-fade-slide-up transition-colors duration-300',
                justDecidedId === a.id && (a.status === 'approved' ? 'bg-emerald-50' : 'bg-red-50'),
              )}
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <CardBody>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <Badge tone="blue">{a.target_type}</Badge>
                      <span className="text-sm font-medium text-ink">{a.action}</span>
                    </div>
                    <p className="mt-1 text-xs text-neutral-500">
                      Requested by {a.requested_by_email ?? 'unknown'} · {formatDate(a.created_at)}
                    </p>
                    {a.decided_by_email && (
                      <p className="mt-0.5 text-xs text-neutral-500">
                        Decided by {a.decided_by_email}
                        {a.decided_at ? ` · ${formatDate(a.decided_at)}` : ''}
                      </p>
                    )}
                    {a.reason && <p className="mt-1 text-xs text-neutral-500">Reason: {a.reason}</p>}
                  </div>
                  <Badge tone={a.status === 'pending' ? 'amber' : a.status === 'approved' ? 'green' : 'red'}>
                    {a.status}
                  </Badge>
                </div>
                {a.status === 'pending' && (
                  <div className="mt-3 flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="Reason (optional)"
                      value={reasonById[a.id] ?? ''}
                      onChange={(e) => setReasonById((prev) => ({ ...prev, [a.id]: e.target.value }))}
                      className="flex-1 rounded-lg border border-neutral-300 bg-surface px-3 py-1.5 text-sm transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-accent-400"
                    />
                    <Button
                      size="sm"
                      variant="secondary"
                      loading={decideMutation.isPending && decideMutation.variables?.id === a.id}
                      onClick={() => decideMutation.mutate({ id: a.id, decision: 'approved' })}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      loading={decideMutation.isPending && decideMutation.variables?.id === a.id}
                      onClick={() => decideMutation.mutate({ id: a.id, decision: 'rejected' })}
                    >
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </Button>
                  </div>
                )}
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

function CollectionsTab() {
  const query = useQuery({ queryKey: ['admin', 'collections'], queryFn: listCollections })

  if (query.isLoading) return <SkeletonRows rows={3} cols={4} />
  if (query.isError) {
    return (
      <StateMessage
        icon={ServerCrash}
        tone="error"
        title="Couldn't load collections"
        description={getApiError(query.error, 'Something went wrong.').message}
        action={
          <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
            Try again
          </Button>
        }
      />
    )
  }

  return (
    <div className="p-6">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-neutral-500">
              <th className="pb-2 pr-4 font-medium">Name</th>
              <th className="pb-2 pr-4 font-medium">Points</th>
              <th className="pb-2 pr-4 font-medium">Status</th>
              <th className="pb-2 pr-4 font-medium" />
            </tr>
          </thead>
          <tbody>
            {query.data!.map((c, i) => (
              <tr
                key={c.name}
                className="animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50"
                style={{ animationDelay: `${i * 40}ms` }}
              >
                <td className="py-2.5 pr-4 font-medium text-ink">
                  <span className="inline-flex items-center gap-1.5">
                    <Database className="h-3.5 w-3.5 text-neutral-400" />
                    {c.name}
                  </span>
                </td>
                <td className="py-2.5 pr-4 tabular-nums text-neutral-600">{c.points_count?.toLocaleString() ?? '—'}</td>
                <td className="py-2.5 pr-4">
                  <span className="inline-flex items-center gap-1.5 text-neutral-600">
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        c.status === 'green' ? 'bg-emerald-500' : c.status === 'yellow' ? 'bg-amber-500' : 'bg-red-500',
                      )}
                    />
                    {c.status}
                  </span>
                </td>
                <td className="py-2.5 pr-4">{c.is_primary && <Badge tone="blue">Primary</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ConsistencyTab() {
  const query = useQuery({
    queryKey: ['admin', 'index-consistency'],
    queryFn: getIndexConsistency,
    enabled: false,
  })

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center gap-3">
        <Button size="sm" variant="secondary" loading={query.isFetching} onClick={() => void query.refetch()}>
          <RefreshCw className={cn('h-3.5 w-3.5', query.isFetching && 'animate-spin')} /> Run consistency check
        </Button>
        {query.data && (
          <p className="animate-fade-slide-up text-sm text-neutral-500">
            Checked {query.data.checked.toLocaleString()} documents, {query.data.inconsistent.length} inconsistent
          </p>
        )}
      </div>

      {query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't run the check"
          description={getApiError(query.error, 'Something went wrong.').message}
        />
      ) : !query.data ? (
        <p className="text-sm text-neutral-400">
          Run a check to flag any document whose Postgres chunk count doesn't match its Qdrant point count.
        </p>
      ) : query.data.inconsistent.length === 0 ? (
        <StateMessage icon={CheckCircle2} title="Everything's consistent" description="No documents flagged." />
      ) : (
        <div className="animate-fade-slide-up overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-neutral-500">
                <th className="pb-2 pr-4 font-medium">Filename</th>
                <th className="pb-2 pr-4 font-medium">Postgres chunks</th>
                <th className="pb-2 pr-4 font-medium">Qdrant points</th>
              </tr>
            </thead>
            <tbody>
              {query.data.inconsistent.map((item, i) => (
                <tr
                  key={item.document_id}
                  className="animate-fade-slide-up border-b border-neutral-100"
                  style={{ animationDelay: `${i * 40}ms` }}
                >
                  <td className="py-2.5 pr-4 font-medium text-ink">
                    <span className="inline-flex items-center gap-1.5">
                      <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                      {item.filename}
                    </span>
                  </td>
                  <td className="py-2.5 pr-4 tabular-nums text-neutral-600">{item.postgres_chunk_count}</td>
                  <td className="py-2.5 pr-4 tabular-nums text-neutral-600">{item.qdrant_point_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function AvailabilityTab() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['admin', 'model-availability'], queryFn: getModelAvailability })

  const toggleMutation = useMutation({
    mutationFn: (disabled: boolean) => setModelAvailability(disabled),
    onSuccess: (data) => {
      toast.success(data.disabled ? 'Model calls disabled' : 'Model calls re-enabled')
      queryClient.setQueryData(['admin', 'model-availability'], data)
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't change model availability.").message),
  })

  if (query.isLoading) return <SkeletonRows rows={1} cols={2} />
  if (query.isError) {
    return (
      <StateMessage
        icon={ServerCrash}
        tone="error"
        title="Couldn't load model availability"
        description={getApiError(query.error, 'Something went wrong.').message}
      />
    )
  }

  const disabled = query.data!.disabled

  return (
    <div className="p-6">
      <Card className="max-w-md animate-fade-slide-up">
        <CardBody className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'relative flex h-9 w-9 items-center justify-center rounded-lg transition-colors duration-300',
                disabled ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-700',
              )}
            >
              {!disabled && (
                <span className="absolute -right-0.5 -top-0.5 flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                </span>
              )}
              <span className="text-sm font-bold">AI</span>
            </div>
            <div>
              <p className="text-sm font-medium text-ink">Claude model calls</p>
              <p className="text-xs text-neutral-500">
                {disabled ? 'Forced unavailable — every model call fails' : 'Operating normally'}
              </p>
            </div>
          </div>
          <Toggle
            checked={!disabled}
            disabled={toggleMutation.isPending}
            label="Claude model calls enabled"
            onChange={(checked) => toggleMutation.mutate(!checked)}
          />
        </CardBody>
      </Card>
      <p className="mt-3 max-w-md text-xs text-neutral-400">
        Testing-only toggle — forces the degraded-retrieval fallback path on demand. Process-local; resets on backend restart.
      </p>
    </div>
  )
}
