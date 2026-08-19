import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardCheck, Play, Plus, ServerCrash, ShieldAlert, Trash2, X } from 'lucide-react'
import { createEvalQuery, deleteEvalQuery, getEvalSummary, listEvalQueries, listEvalRuns, runEvalQuery } from '@/api/evaluation'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Card, CardBody } from '@/components/ui/Card'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { EvalQuery } from '@/types/evaluation'

function pct(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(0)}%`
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function EvaluationPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<EvalQuery | null>(null)
  const [creating, setCreating] = useState(false)
  const [justRunId, setJustRunId] = useState<string | null>(null)

  const summaryQuery = useQuery({ queryKey: ['eval', 'summary'], queryFn: getEvalSummary })
  const queriesQuery = useQuery({ queryKey: ['eval', 'queries'], queryFn: () => listEvalQueries() })

  const isForbidden = queriesQuery.isError && getApiError(queriesQuery.error).status === 403

  const runMutation = useMutation({
    mutationFn: (id: string) => runEvalQuery(id, 10),
    onSuccess: (_run, id) => {
      toast.success('Evaluation run complete')
      void queryClient.invalidateQueries({ queryKey: ['eval'] })
      setJustRunId(id)
      window.setTimeout(() => setJustRunId((current) => (current === id ? null : current)), 2200)
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't run that evaluation.").message),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteEvalQuery,
    onSuccess: () => {
      setSelected(null)
      toast.success('Query deleted')
      void queryClient.invalidateQueries({ queryKey: ['eval', 'queries'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't delete that query.").message),
  })

  if (isForbidden) {
    return (
      <div>
        <PageHeader title="Evaluation" description="RAG quality evaluation runs" />
        <StateMessage
          icon={ShieldAlert}
          tone="error"
          title="Restricted"
          description="Running evaluations makes real, cost-bearing model calls, so this tool is limited to Admin, CEO, and Project Manager regardless of analytics access."
          className="min-h-[50vh]"
        />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Evaluation"
        description="RAG quality evaluation runs"
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New query
          </Button>
        }
      />

      {summaryQuery.data && summaryQuery.data.run_count > 0 && (
        <div className="grid grid-cols-2 gap-3 p-6 pb-0 sm:grid-cols-4 lg:grid-cols-6">
          <SummaryStat index={0} label="Runs" value={summaryQuery.data.run_count} />
          <SummaryStat index={1} label="Recall@k" display={pct(summaryQuery.data.avg_recall_at_k)} />
          <SummaryStat index={2} label="Precision@k" display={pct(summaryQuery.data.avg_precision_at_k)} />
          <SummaryStat index={3} label="MRR" display={pct(summaryQuery.data.avg_mrr)} />
          <SummaryStat index={4} label="Groundedness" display={pct(summaryQuery.data.avg_groundedness)} />
          <SummaryStat index={5} label="Hallucination" display={pct(summaryQuery.data.avg_hallucination_rate)} />
        </div>
      )}

      {queriesQuery.isLoading ? (
        <SkeletonRows rows={5} cols={4} />
      ) : queriesQuery.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load eval queries"
          description={getApiError(queriesQuery.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void queriesQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : queriesQuery.data && queriesQuery.data.length > 0 ? (
        <div className="flex">
          <div className="flex-1 overflow-x-auto p-6">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="pb-2 pr-4 font-medium">Query</th>
                  <th className="pb-2 pr-4 font-medium">Categories</th>
                  <th className="pb-2 pr-4 font-medium">Created</th>
                  <th className="pb-2 pr-4 font-medium" />
                </tr>
              </thead>
              <tbody>
                {queriesQuery.data.map((q, i) => (
                  <tr
                    key={q.id}
                    onClick={() => setSelected(q)}
                    className={cn(
                      'animate-fade-slide-up cursor-pointer border-b border-neutral-100 transition-colors duration-300',
                      justRunId === q.id ? 'bg-emerald-50' : 'hover:bg-neutral-50',
                      selected?.id === q.id && 'bg-accent-50/60',
                    )}
                    style={{ animationDelay: `${i * 30}ms` }}
                  >
                    <td className="py-2.5 pr-4 font-medium text-ink">{q.query}</td>
                    <td className="py-2.5 pr-4 text-neutral-600">{q.categories.join(', ') || '—'}</td>
                    <td className="py-2.5 pr-4 text-neutral-600">{formatDate(q.created_at)}</td>
                    <td className="py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={runMutation.isPending && runMutation.variables === q.id}
                          onClick={(e) => {
                            e.stopPropagation()
                            runMutation.mutate(q.id)
                          }}
                        >
                          <Play className="h-3.5 w-3.5" /> Run
                        </Button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            deleteMutation.mutate(q.id)
                          }}
                          className="rounded-md p-1.5 text-neutral-400 transition-colors duration-150 hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && <RunHistoryPanel evalQuery={selected} onClose={() => setSelected(null)} />}
        </div>
      ) : (
        <StateMessage
          icon={ClipboardCheck}
          title="No eval queries yet"
          description="Add a query with known-good chunk ids to start measuring retrieval and answer quality."
        />
      )}

      {creating && <CreateQueryPanel onClose={() => setCreating(false)} />}
    </div>
  )
}

function SummaryStat({ label, value, display, index }: { label: string; value?: number; display?: string; index: number }) {
  const animated = useCountUp(value ?? null)
  return (
    <Card className="animate-fade-slide-up" style={{ animationDelay: `${index * 50}ms` }}>
      <CardBody className="px-3 py-3">
        <p className="text-lg font-semibold tabular-nums text-ink">{value != null ? animated.toLocaleString() : display}</p>
        <p className="text-xs text-neutral-500">{label}</p>
      </CardBody>
    </Card>
  )
}

function RunHistoryPanel({ evalQuery, onClose }: { evalQuery: EvalQuery; onClose: () => void }) {
  const runsQuery = useQuery({
    queryKey: ['eval', 'runs', evalQuery.id],
    queryFn: () => listEvalRuns(evalQuery.id, 20),
  })

  return (
    <div className="w-96 shrink-0 animate-slide-in-right border-l border-neutral-200 p-5">
      <div className="mb-3 flex items-start justify-between">
        <h3 className="text-sm font-semibold text-ink">Run history</h3>
        <button type="button" onClick={onClose} className="text-xs text-neutral-400 transition-colors hover:text-ink">
          Close
        </button>
      </div>
      <p className="mb-4 text-xs text-neutral-500">{evalQuery.query}</p>

      {runsQuery.isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-neutral-100" />
          ))}
        </div>
      ) : runsQuery.data && runsQuery.data.length > 0 ? (
        <div className="space-y-3">
          {runsQuery.data.map((run, i) => (
            <Card key={run.id} className="animate-fade-slide-up" style={{ animationDelay: `${i * 40}ms` }}>
              <CardBody className="space-y-1.5 px-3 py-3 text-xs">
                <p className="text-neutral-400">{formatDate(run.created_at)}</p>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                  <RunStat label="Recall@k" value={pct(run.recall_at_k)} />
                  <RunStat label="Precision@k" value={pct(run.precision_at_k)} />
                  <RunStat label="MRR" value={pct(run.mrr)} />
                  <RunStat label="Groundedness" value={pct(run.groundedness)} />
                  <RunStat label="Hallucination" value={pct(run.hallucination_rate)} />
                  <RunStat label="Cost" value={run.cost_usd != null ? `$${run.cost_usd.toFixed(4)}` : '—'} />
                </div>
                {run.generated_answer && (
                  <p className="mt-2 line-clamp-3 text-neutral-600">{run.generated_answer}</p>
                )}
              </CardBody>
            </Card>
          ))}
        </div>
      ) : (
        <p className="text-sm text-neutral-400">No runs yet — click Run to evaluate this query.</p>
      )}
    </div>
  )
}

function RunStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-neutral-500">{label}</span>
      <span className="font-medium tabular-nums text-ink">{value}</span>
    </div>
  )
}

function CreateQueryPanel({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [description, setDescription] = useState('')

  const createMutation = useMutation({
    mutationFn: () => createEvalQuery({ query, description: description || undefined }),
    onSuccess: () => {
      toast.success('Eval query created')
      void queryClient.invalidateQueries({ queryKey: ['eval', 'queries'] })
      onClose()
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't create that query.").message),
  })

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4 animate-fade-slide-up" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-neutral-200 bg-surface p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-ink">New eval query</h3>
          <button type="button" onClick={onClose} className="text-neutral-400 hover:text-ink">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-500">Query</label>
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="What is our leave policy?" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-500">Description (optional)</label>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Why this query matters" />
          </div>
          <Button className="w-full" loading={createMutation.isPending} disabled={!query.trim()} onClick={() => createMutation.mutate()}>
            <Plus className="h-4 w-4" /> Create
          </Button>
        </div>
      </div>
    </div>
  )
}
