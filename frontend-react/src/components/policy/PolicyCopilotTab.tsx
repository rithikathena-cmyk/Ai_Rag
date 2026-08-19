import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Send, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react'
import {
  approveCopilotProposal, listCopilotPolicies, rejectCopilotProposal, sendCopilotMessage,
} from '@/api/policyCopilot'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import type { CopilotChatResponse, CopilotImpact, RiskLevel } from '@/types/policyCopilot'

const RISK_TONE: Record<RiskLevel, 'green' | 'amber' | 'red'> = {
  LOW: 'green', MEDIUM: 'amber', HIGH: 'red', CRITICAL: 'red',
}

const ACTION_TONE: Record<string, 'green' | 'amber' | 'blue' | 'red' | 'neutral'> = {
  ALLOW: 'red', FLAG: 'amber', MASK: 'blue', REDACT: 'blue', BLOCK: 'green', ESCALATE: 'amber',
}

interface Turn {
  role: 'admin' | 'copilot'
  text: string
  response?: CopilotChatResponse
}

const EXAMPLES = [
  'What can HR see?',
  'Who can access audit logs?',
  'What guardrails do you have?',
  'Show me all PII policies',
  'Why are credit cards redacted?',
  'Mask phone numbers in output',
]

export function PolicyCopilotTab() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const queryClient = useQueryClient()
  const endRef = useRef<HTMLDivElement>(null)

  const policies = useQuery({ queryKey: ['copilot', 'policies'], queryFn: listCopilotPolicies })

  const chat = useMutation({
    mutationFn: sendCopilotMessage,
    onSuccess: (response, message) => {
      setTurns((prev) => [
        ...prev,
        { role: 'admin', text: message },
        { role: 'copilot', text: response.reply, response },
      ])
      setDraft('')
      void queryClient.invalidateQueries({ queryKey: ['copilot'] })
      window.setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    },
    onError: (err) => toast.error(getApiError(err, "The Copilot couldn't handle that.").message),
  })

  const approve = useMutation({
    mutationFn: (id: string) => approveCopilotProposal(id),
    onSuccess: (data) => {
      const n = Array.isArray(data?.applied) ? data.applied.length : 0
      toast.success(`Policy applied — ${n} row${n === 1 ? '' : 's'} updated, now in force`)
      void queryClient.invalidateQueries({ queryKey: ['copilot'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't apply that proposal.").message),
  })

  const reject = useMutation({
    mutationFn: (id: string) => rejectCopilotProposal(id),
    onSuccess: () => {
      toast.success('Proposal rejected')
      void queryClient.invalidateQueries({ queryKey: ['copilot'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't reject that proposal.").message),
  })

  function submit() {
    const message = draft.trim()
    if (message && !chat.isPending) chat.mutate(message)
  }

  return (
    <div className="grid gap-6 p-6 lg:grid-cols-[1fr_20rem]">
      <div className="flex flex-col gap-4">
        <div className="rounded-lg border border-neutral-200 bg-surface">
          <div className="border-b border-neutral-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-ink">Policy Copilot</h3>
            <p className="mt-0.5 text-xs text-neutral-500">
              Describe a policy change in plain language. Nothing is applied directly — every change
              becomes a proposal you review and approve.
            </p>
          </div>

          <div className="max-h-[30rem] space-y-4 overflow-y-auto p-4">
            {turns.length === 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium text-neutral-500">Try:</p>
                {EXAMPLES.map((e) => (
                  <button
                    key={e}
                    type="button"
                    onClick={() => setDraft(e)}
                    className="block w-full rounded-md border border-neutral-200 px-3 py-1.5 text-left text-xs text-neutral-600 transition-colors hover:border-accent-300 hover:bg-accent-50"
                  >
                    {e}
                  </button>
                ))}
              </div>
            )}

            {turns.map((turn, i) => (
              <div key={i} className={cn('text-sm', turn.role === 'admin' && 'text-right')}>
                <div
                  className={cn(
                    'inline-block max-w-[85%] rounded-lg px-3 py-2',
                    turn.role === 'admin'
                      ? 'bg-accent-600 text-white'
                      : 'bg-neutral-100 text-ink',
                    // Answers are assembled server-side as aligned plain text
                    // (policy tables, permission lists). Without preserving
                    // whitespace the columns collapse into one run-on line.
                    turn.role === 'copilot' && 'w-full max-w-full whitespace-pre-wrap',
                    // Monospace only when the answer actually contains an
                    // aligned table — prose answers read better proportional.
                    turn.role === 'copilot' && hasAlignedColumns(turn.text) && 'font-mono text-[11px] leading-relaxed',
                  )}
                >
                  {turn.text}
                </div>
                {turn.response && (
                  <ResponseDetail
                    response={turn.response}
                    onApprove={(id) => approve.mutate(id)}
                    onReject={(id) => reject.mutate(id)}
                    busy={approve.isPending || reject.isPending}
                  />
                )}
              </div>
            ))}
            <div ref={endRef} />
          </div>

          <div className="flex gap-2 border-t border-neutral-200 p-3">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              placeholder="Ask me to configure a PII policy..."
              className="flex-1 rounded-lg border border-neutral-300 bg-surface px-3 py-2 text-sm focus:border-accent-500 focus:outline-none focus:ring-2 focus:ring-accent-400"
            />
            <Button onClick={submit} disabled={chat.isPending || !draft.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <ActivePolicySidebar rows={policies.data?.policies ?? []} loading={policies.isLoading} />
    </div>
  )
}


/** True when the answer contains an aligned column layout (a policy table or
 *  permission list), which only reads correctly in a monospace face. Prose
 *  answers stay proportional. */
function hasAlignedColumns(text: string): boolean {
  const NEWLINE = String.fromCharCode(10)
  return text
    .split(NEWLINE)
    .some((line) => line.startsWith('  ') && line.trim().length > 0)
}

function ResponseDetail({
  response,
  onApprove,
  onReject,
  busy,
}: {
  response: CopilotChatResponse
  onApprove: (id: string) => void
  onReject: (id: string) => void
  busy: boolean
}) {
  if (response.errors.length > 0 && !response.proposal_id) {
    return (
      <div className="mt-2 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-left text-xs text-red-800">
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <div>
          {response.errors.map((e) => <p key={e}>{e}</p>)}
          <p className="mt-1 text-red-600">Nothing was changed.</p>
        </div>
      </div>
    )
  }

  if (!response.proposal_id) return null

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-neutral-300 bg-surface p-3 text-left">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Policy proposal
        </span>
        <Badge tone={RISK_TONE[response.risk]}>{response.risk} RISK</Badge>
      </div>

      {response.impacts.map((impact) => <ImpactCard key={`${impact.entity}-${impact.location}`} impact={impact} />)}

      {response.warnings.length > 0 && (
        <div className="space-y-1">
          {response.warnings.map((w) => (
            <p key={w} className="flex items-start gap-1.5 text-xs text-amber-700">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              {w}
            </p>
          ))}
        </div>
      )}

      <div className="space-y-2 border-t border-neutral-200 pt-2">
        {/* Approving APPLIES the change — it is the one action here that
            alters live enforcement, so the copy says so plainly rather than
            reading like a form submission. */}
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-neutral-500">
            {response.requires_approval
              ? 'Explicit approval required · not applied'
              : 'Pending approval · not applied'}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onReject(response.proposal_id!)}>
              Reject
            </Button>
            <Button size="sm" disabled={busy} onClick={() => onApprove(response.proposal_id!)}>
              {busy ? 'Applying…' : 'Approve & apply'}
            </Button>
          </div>
        </div>
        {response.risk === 'CRITICAL' && (
          <p className="text-[11px] text-red-700">
            Approving this weakens protection for a critical entity. It takes effect immediately
            {response.role_exceptions.length > 0
              ? ', including the role exceptions above.'
              : ' for every role.'}
          </p>
        )}
      </div>
    </div>
  )
}

function ImpactCard({ impact }: { impact: CopilotImpact }) {
  const weakens = impact.direction === 'WEAKENS'
  return (
    <div className={cn('rounded-md border p-2.5', weakens ? 'border-red-200 bg-red-50/50' : 'border-neutral-200')}>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-mono font-semibold text-ink">{impact.entity}</span>
        <span className="text-neutral-400">{impact.location}</span>
        <Badge tone={ACTION_TONE[impact.current_action] ?? 'neutral'}>{impact.current_action}</Badge>
        <span className="text-neutral-400">→</span>
        <Badge tone={ACTION_TONE[impact.proposed_action] ?? 'neutral'}>{impact.proposed_action}</Badge>
        {impact.reveal_last != null && (
          <span className="text-[11px] text-neutral-500">last {impact.reveal_last} visible</span>
        )}
        {weakens && (
          <span className="ml-auto flex items-center gap-1 text-[11px] font-semibold text-red-700">
            <ShieldAlert className="h-3 w-3" /> WEAKENS
          </span>
        )}
      </div>

      {impact.current_sample && (
        <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[11px]">
          <div>
            <p className="mb-0.5 font-sans text-[10px] uppercase tracking-wide text-neutral-400">Now</p>
            <p className="rounded bg-neutral-100 px-1.5 py-1 text-neutral-700">{impact.current_sample}</p>
          </div>
          <div>
            <p className="mb-0.5 font-sans text-[10px] uppercase tracking-wide text-neutral-400">Proposed</p>
            <p className={cn('rounded px-1.5 py-1', weakens ? 'bg-red-100 text-red-800' : 'bg-neutral-100 text-neutral-700')}>
              {impact.proposed_sample}
            </p>
          </div>
        </div>
      )}

      {impact.role_effects.some((e) => e.is_exception) && (
        <div className="mt-2">
          <p className="mb-1 text-[10px] uppercase tracking-wide text-neutral-400">What each role sees</p>
          <div className="overflow-hidden rounded border border-neutral-200">
            {impact.role_effects.map((effect) => (
              <div
                key={effect.role}
                className={cn(
                  'flex items-center gap-2 border-b border-neutral-100 px-2 py-1 text-[11px] last:border-b-0',
                  effect.is_exception && 'bg-amber-50',
                )}
              >
                <span className="w-28 shrink-0 text-neutral-600">{effect.label}</span>
                <Badge tone={ACTION_TONE[effect.action] ?? 'neutral'}>{effect.action}</Badge>
                <span className="truncate font-mono text-neutral-700">{effect.sample}</span>
                {effect.is_exception && (
                  <span className="ml-auto shrink-0 font-semibold text-amber-700">exception</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="mt-2 text-[11px] text-neutral-500">
        Risk {impact.risk} · exposure {impact.exposure} · {impact.affected_roles.length} roles · {impact.blast_radius}
      </p>
      {impact.notes.map((n) => (
        <p key={n} className="mt-1 text-[11px] text-neutral-500">{n}</p>
      ))}
    </div>
  )
}

function ActivePolicySidebar({ rows, loading }: { rows: CopilotPolicyRowLike[]; loading: boolean }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-surface">
      <div className="border-b border-neutral-200 px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Active PII policy</h3>
      </div>
      <div className="max-h-[30rem] divide-y divide-neutral-100 overflow-y-auto">
        {loading && <p className="p-3 text-xs text-neutral-400">Loading…</p>}
        {rows.map((r) => (
          <div key={r.entity} className="px-3 py-2">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[11px] font-semibold text-ink">{r.entity}</span>
              {r.critical && <Badge tone="red">critical</Badge>}
              {!r.enforceable && <Badge tone="amber">no detector</Badge>}
            </div>
            <p className="mt-0.5 text-[11px] text-neutral-500">
              in <span className="font-mono">{r.input_action}</span> · out{' '}
              <span className="font-mono">{r.output_action}</span>
              <span className="text-neutral-400"> · {r.source}</span>
              {r.reveal_last != null && (
                <span className="text-neutral-400"> · last {r.reveal_last} visible</span>
              )}
            </p>
            {/* Only the slots that differ from the base are sent, so each is
                rendered only when present — printing an empty "in" would read
                as a role with no input policy at all. */}
            {Object.entries(r.role_overrides ?? {}).map(([role, actions]) => (
              <p key={role} className="mt-0.5 text-[10px] text-amber-700">
                {role}:{' '}
                {[
                  actions.input_action && `in ${actions.input_action}`,
                  actions.output_action && `out ${actions.output_action}`,
                  actions.reveal_last != null && `last ${actions.reveal_last} visible`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            ))}
            {r.disabled_row_present && (
              <p className="mt-0.5 flex items-start gap-1 text-[10px] text-amber-700">
                <ShieldCheck className="mt-0.5 h-2.5 w-2.5 shrink-0" />
                disabled rule — safe default in force
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

type CopilotPolicyRowLike = import('@/types/policyCopilot').CopilotPolicyRow
