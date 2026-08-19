import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, ChevronRight, ListChecks, ScrollText, ServerCrash, Shield } from 'lucide-react'
import { listAuditEvents, listUploadLogs } from '@/api/auditLogs'
import { getGatewayUsage, getGuardrailAnalytics } from '@/api/metrics'
import { getApiError } from '@/lib/apiError'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { Tabs } from '@/components/ui/Tabs'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { AuditEvent } from '@/types/auditLogs'
import type { GatewayUsageSample } from '@/types/metrics'

const TAB_OPTIONS = [
  { value: 'requests', label: 'Requests' },
  { value: 'uploads', label: 'Document uploads' },
  { value: 'guardrails', label: 'Guardrail events' },
  { value: 'activity', label: 'Activity' },
]

const OUTCOME_TONE: Record<string, 'green' | 'red' | 'amber' | 'neutral'> = {
  success: 'green',
  rejected: 'red',
  failed: 'red',
  error: 'red',
}

// event_types.py's taxonomy, grouped to match that module's own section
// comments — kept as display labels only, the backend is the source of
// truth for which values are valid (this is a filter convenience, not
// validation).
const AUDIT_EVENT_TYPE_GROUPS: { label: string; values: string[] }[] = [
  { label: 'Authentication', values: ['LOGIN_SUCCESS', 'LOGIN_FAILURE', 'LOGOUT', 'SESSION_CREATED', 'SESSION_EXPIRED'] },
  { label: 'Authorization', values: ['ACCESS_GRANTED', 'ACCESS_DENIED', 'RBAC_DENIED', 'RESOURCE_ACCESS_DENIED'] },
  {
    label: 'Documents',
    values: [
      'DOCUMENT_UPLOAD', 'DOCUMENT_UPLOAD_FAILED', 'DOCUMENT_DELETE', 'DOCUMENT_ACCESS', 'DOCUMENT_DOWNLOAD',
      'DOCUMENT_PROCESSING_STARTED', 'DOCUMENT_PROCESSING_COMPLETED', 'DOCUMENT_PROCESSING_FAILED',
    ],
  },
  { label: 'RAG', values: ['SEARCH_STARTED', 'SEARCH_COMPLETED', 'RETRIEVAL_DENIED', 'DOCUMENT_RETRIEVAL', 'CITATION_GENERATED'] },
  { label: 'Chat / LLM', values: ['CONVERSATION_CREATED', 'MESSAGE_SENT', 'LLM_REQUEST', 'LLM_RESPONSE', 'LLM_ERROR'] },
  {
    label: 'Guardrails',
    values: [
      'GUARDRAIL_STARTED', 'GUARDRAIL_PII_DETECTED', 'GUARDRAIL_INJECTION_DETECTED', 'GUARDRAIL_SCOPE_DENIED',
      'GUARDRAIL_POLICY_DENIED', 'GUARDRAIL_OUTPUT_BLOCKED', 'GUARDRAIL_COMPLETED',
    ],
  },
  { label: 'Security', values: ['RATE_LIMIT_EXCEEDED', 'SUSPICIOUS_ACTIVITY', 'POLICY_VIOLATION'] },
  { label: 'System', values: ['API_ERROR', 'INTERNAL_ERROR', 'SERVICE_UNAVAILABLE'] },
]

const AUDIT_OUTCOME_FILTERS = [
  { value: undefined, label: 'All' },
  { value: 'SUCCESS', label: 'Success' },
  { value: 'FAILURE', label: 'Failure' },
  { value: 'DENIED', label: 'Denied' },
  { value: 'BLOCKED', label: 'Blocked' },
  { value: 'ERROR', label: 'Error' },
] as const

const AUDIT_OUTCOME_TONE: Record<string, 'green' | 'red' | 'amber' | 'neutral'> = {
  SUCCESS: 'green',
  FAILURE: 'red',
  DENIED: 'red',
  BLOCKED: 'amber',
  ERROR: 'red',
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function AuditLogsPage() {
  const [tab, setTab] = useState('requests')

  return (
    <div>
      <PageHeader title="Audit Logs" description="Every request, upload, and guardrail decision in one place" />
      <Tabs options={TAB_OPTIONS} value={tab} onChange={setTab} />
      <div key={tab} className="animate-fade-slide-up">
        {tab === 'requests' && <RequestsTab />}
        {tab === 'uploads' && <UploadsTab />}
        {tab === 'guardrails' && <GuardrailsTab />}
        {tab === 'activity' && <ActivityTab />}
      </div>
    </div>
  )
}

const DECISION_FILTERS = [
  { value: undefined, label: 'All' },
  { value: 'allowed', label: 'Allowed' },
  { value: 'denied', label: 'Denied' },
] as const

function RequestsTab() {
  const [decision, setDecision] = useState<string | undefined>(undefined)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['audit-logs', 'requests', decision],
    queryFn: () => getGatewayUsage(200, decision),
  })
  const deniedTotal = useCountUp(query.data?.denied_count)

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex gap-1.5">
          {DECISION_FILTERS.map((f) => (
            <button
              key={f.label}
              type="button"
              onClick={() => setDecision(f.value)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-medium transition-colors duration-150',
                decision === f.value ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        {query.data && query.data.denied_count > 0 && (
          <Badge tone="red" className="tabular-nums">
            {deniedTotal.toLocaleString()} denied (all time)
          </Badge>
        )}
      </div>

      {query.isLoading ? (
        <SkeletonRows rows={8} cols={6} />
      ) : query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load requests"
          description={getApiError(query.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : query.data!.samples.length === 0 ? (
        <StateMessage icon={ListChecks} title="No requests recorded yet" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-neutral-500">
                <th className="pb-2 pr-4 font-medium" />
                <th className="pb-2 pr-4 font-medium">When</th>
                <th className="pb-2 pr-4 font-medium">User</th>
                <th className="pb-2 pr-4 font-medium">Agent</th>
                <th className="pb-2 pr-4 font-medium">Capability</th>
                <th className="pb-2 pr-4 font-medium">Decision</th>
                <th className="pb-2 pr-4 font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {query.data!.samples.map((r, i) => (
                <RequestRow
                  key={r.id}
                  sample={r}
                  index={i}
                  expanded={expandedId === r.id}
                  onToggle={() => setExpandedId((cur) => (cur === r.id ? null : r.id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function RequestRow({
  sample,
  index,
  expanded,
  onToggle,
}: {
  sample: GatewayUsageSample
  index: number
  expanded: boolean
  onToggle: () => void
}) {
  const hasDetail = Boolean(
    sample.denial_reason || sample.tool_calls?.length || sample.documents_retrieved?.length,
  )

  return (
    <>
      <tr
        onClick={hasDetail ? onToggle : undefined}
        className={cn(
          'animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150',
          hasDetail && 'cursor-pointer hover:bg-neutral-50',
          expanded && 'bg-accent-50/60',
        )}
        style={{ animationDelay: `${Math.min(index, 20) * 25}ms` }}
      >
        <td className="py-2.5 pl-1">
          {hasDetail && (
            <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform duration-150', expanded && 'rotate-90')} />
          )}
        </td>
        <td className="py-2.5 pr-4 text-neutral-600">{formatDate(sample.created_at)}</td>
        <td className="py-2.5 pr-4">
          <div className="font-medium text-ink">
            {sample.user_email ?? (sample.role ? 'Unattributed' : 'System')}
          </div>
          {sample.role && <div className="text-xs text-neutral-400">{sample.role}{sample.department ? ` · ${sample.department}` : ''}</div>}
        </td>
        <td className="py-2.5 pr-4 text-neutral-600">{sample.agent_name}</td>
        <td className="py-2.5 pr-4 text-neutral-600">{sample.requested_capability ?? '—'}</td>
        <td className="py-2.5 pr-4">
          <Badge tone={sample.decision === 'denied' ? 'red' : 'green'}>{sample.decision}</Badge>
        </td>
        <td className="py-2.5 pr-4 tabular-nums text-neutral-600">{sample.cost_usd > 0 ? `$${sample.cost_usd.toFixed(4)}` : '—'}</td>
      </tr>
      {expanded && (
        <tr className="animate-fade-slide-up border-b border-neutral-100 bg-neutral-50/60">
          <td colSpan={7} className="px-4 py-3 text-xs">
            <dl className="space-y-1.5">
              {sample.denial_reason && (
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-neutral-500">Denial reason</dt>
                  <dd className="text-red-700">{sample.denial_reason}</dd>
                </div>
              )}
              {sample.tool_calls && sample.tool_calls.length > 0 && (
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-neutral-500">Tool calls</dt>
                  <dd className="flex flex-wrap gap-1">
                    {sample.tool_calls.map((t, i) => (
                      <Badge key={i} tone="neutral">{t}</Badge>
                    ))}
                  </dd>
                </div>
              )}
              {sample.documents_retrieved && sample.documents_retrieved.length > 0 && (
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-neutral-500">Documents retrieved</dt>
                  <dd className="text-neutral-600">{sample.documents_retrieved.join(', ')}</dd>
                </div>
              )}
              <div className="flex gap-2">
                <dt className="shrink-0 font-medium text-neutral-500">Request ID</dt>
                <dd className="text-neutral-400">{sample.request_id}</dd>
              </div>
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function UploadsTab() {
  const query = useQuery({ queryKey: ['audit-logs', 'uploads'], queryFn: () => listUploadLogs() })

  if (query.isLoading) return <SkeletonRows rows={7} cols={4} />
  if (query.isError) {
    return (
      <StateMessage
        icon={ServerCrash}
        tone="error"
        title="Couldn't load upload logs"
        description={getApiError(query.error, 'Something went wrong.').message}
        action={
          <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
            Try again
          </Button>
        }
      />
    )
  }
  if (query.data!.items.length === 0) {
    return <StateMessage icon={ScrollText} title="No upload activity yet" />
  }

  return (
    <div className="overflow-x-auto p-6">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-neutral-200 text-neutral-500">
            <th className="pb-2 pr-4 font-medium">Filename</th>
            <th className="pb-2 pr-4 font-medium">Outcome</th>
            <th className="pb-2 pr-4 font-medium">Error</th>
            <th className="pb-2 pr-4 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {query.data!.items.map((log, i) => (
            <tr
              key={log.id}
              className="animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50"
              style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}
            >
              <td className="py-2.5 pr-4 font-medium text-ink">{log.filename ?? '—'}</td>
              <td className="py-2.5 pr-4">
                <Badge tone={OUTCOME_TONE[log.outcome] ?? 'neutral'}>{log.outcome}</Badge>
              </td>
              <td className="py-2.5 pr-4 text-neutral-500">{log.error_message ?? '—'}</td>
              <td className="py-2.5 pr-4 text-neutral-600">{formatDate(log.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GuardrailsTab() {
  const query = useQuery({ queryKey: ['audit-logs', 'guardrails'], queryFn: getGuardrailAnalytics })

  if (query.isLoading) return <SkeletonRows rows={7} cols={4} />
  if (query.isError) {
    return (
      <StateMessage
        icon={ServerCrash}
        tone="error"
        title="Couldn't load guardrail events"
        description={getApiError(query.error, 'Something went wrong.').message}
        action={
          <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
            Try again
          </Button>
        }
      />
    )
  }
  if (query.data!.events.length === 0) {
    return <StateMessage icon={Shield} title="No guardrail events yet" />
  }

  return (
    <div className="overflow-x-auto p-6">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-neutral-200 text-neutral-500">
            <th className="pb-2 pr-4 font-medium">Direction</th>
            <th className="pb-2 pr-4 font-medium">Check</th>
            <th className="pb-2 pr-4 font-medium">Action</th>
            <th className="pb-2 pr-4 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {[...query.data!.events].reverse().map((e, i) => (
            <tr
              key={i}
              className="animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50"
              style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}
            >
              <td className="py-2.5 pr-4 text-neutral-600">{e.direction}</td>
              <td className="py-2.5 pr-4 text-neutral-600">{e.check}</td>
              <td className="py-2.5 pr-4">
                <Badge tone={e.action === 'block' ? 'red' : e.action === 'redact' ? 'amber' : 'green'}>{e.action}</Badge>
              </td>
              <td className="py-2.5 pr-4 text-neutral-500">{e.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ActivityTab() {
  const [eventType, setEventType] = useState<string | undefined>(undefined)
  const [outcome, setOutcome] = useState<string | undefined>(undefined)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['audit-logs', 'activity', eventType, outcome, dateFrom, dateTo],
    queryFn: () =>
      listAuditEvents({
        event_type: eventType,
        outcome,
        date_from: dateFrom || undefined,
        date_to: dateTo ? `${dateTo}T23:59:59` : undefined,
        limit: 100,
      }),
  })

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex gap-1.5">
          {AUDIT_OUTCOME_FILTERS.map((f) => (
            <button
              key={f.label}
              type="button"
              onClick={() => setOutcome(f.value)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-medium transition-colors duration-150',
                outcome === f.value ? 'bg-accent-600 text-white' : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <select
          value={eventType ?? ''}
          onChange={(e) => setEventType(e.target.value || undefined)}
          className="rounded-md border border-neutral-200 bg-white px-2.5 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
        >
          <option value="">All event types</option>
          {AUDIT_EVENT_TYPE_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.values.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <div className="flex items-center gap-1.5 text-xs text-neutral-500">
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="rounded-md border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
          />
          <span>–</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="rounded-md border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-600 focus:border-accent-500 focus:outline-none"
          />
        </div>
      </div>

      {query.isLoading ? (
        <SkeletonRows rows={8} cols={7} />
      ) : query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load activity"
          description={getApiError(query.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : query.data!.items.length === 0 ? (
        <StateMessage icon={Activity} title="No activity recorded yet" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="pb-2 pr-4 font-medium" />
                  <th className="pb-2 pr-4 font-medium">Time</th>
                  <th className="pb-2 pr-4 font-medium">Event</th>
                  <th className="pb-2 pr-4 font-medium">Actor</th>
                  <th className="pb-2 pr-4 font-medium">Role</th>
                  <th className="pb-2 pr-4 font-medium">Resource</th>
                  <th className="pb-2 pr-4 font-medium">Action</th>
                  <th className="pb-2 pr-4 font-medium">Outcome</th>
                  <th className="pb-2 pr-4 font-medium">Request ID</th>
                </tr>
              </thead>
              <tbody>
                {query.data!.items.map((event, i) => (
                  <ActivityRow
                    key={event.event_id}
                    event={event}
                    index={i}
                    expanded={expandedId === event.event_id}
                    onToggle={() => setExpandedId((cur) => (cur === event.event_id ? null : event.event_id))}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {query.data!.total > query.data!.items.length && (
            <p className="mt-3 text-xs text-neutral-400">
              Showing {query.data!.items.length} of {query.data!.total.toLocaleString()} — narrow the filters above to see more.
            </p>
          )}
        </>
      )}
    </div>
  )
}

function ActivityRow({
  event,
  index,
  expanded,
  onToggle,
}: {
  event: AuditEvent
  index: number
  expanded: boolean
  onToggle: () => void
}) {
  const metadataEntries = Object.entries(event.metadata ?? {})
  const hasDetail = Boolean(event.reason_code || metadataEntries.length > 0)

  return (
    <>
      <tr
        onClick={hasDetail ? onToggle : undefined}
        className={cn(
          'animate-fade-slide-up border-b border-neutral-100 transition-colors duration-150',
          hasDetail && 'cursor-pointer hover:bg-neutral-50',
          expanded && 'bg-accent-50/60',
        )}
        style={{ animationDelay: `${Math.min(index, 20) * 25}ms` }}
      >
        <td className="py-2.5 pl-1">
          {hasDetail && (
            <ChevronRight className={cn('h-3.5 w-3.5 text-neutral-400 transition-transform duration-150', expanded && 'rotate-90')} />
          )}
        </td>
        <td className="py-2.5 pr-4 text-neutral-600">{formatDate(event.created_at)}</td>
        <td className="py-2.5 pr-4 font-medium text-ink">{event.event_type}</td>
        <td className="py-2.5 pr-4 text-neutral-600">
          {event.actor_email ?? (event.actor_role ? 'Unattributed' : 'System')}
        </td>
        <td className="py-2.5 pr-4 text-neutral-500">{event.actor_role ?? '—'}</td>
        <td className="py-2.5 pr-4 text-neutral-500">
          {event.resource_type ? `${event.resource_type}${event.resource_id ? ` · ${event.resource_id}` : ''}` : '—'}
        </td>
        <td className="py-2.5 pr-4 text-neutral-500">{event.action ?? '—'}</td>
        <td className="py-2.5 pr-4">
          <Badge tone={AUDIT_OUTCOME_TONE[event.outcome] ?? 'neutral'}>{event.outcome}</Badge>
        </td>
        <td className="py-2.5 pr-4 text-xs text-neutral-400">{event.request_id}</td>
      </tr>
      {expanded && (
        <tr className="animate-fade-slide-up border-b border-neutral-100 bg-neutral-50/60">
          <td colSpan={9} className="px-4 py-3 text-xs">
            <dl className="space-y-1.5">
              {event.reason_code && (
                <div className="flex gap-2">
                  <dt className="shrink-0 font-medium text-neutral-500">Reason</dt>
                  <dd className="text-neutral-600">{event.reason_code}</dd>
                </div>
              )}
              {metadataEntries.map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <dt className="shrink-0 font-medium text-neutral-500">{key}</dt>
                  <dd className="text-neutral-600">{String(value)}</dd>
                </div>
              ))}
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}
