import { api } from '@/api/client'
import type { EvalQuery, EvalRun, EvalSummary } from '@/types/evaluation'

export async function listEvalQueries(limit = 100, offset = 0): Promise<EvalQuery[]> {
  const { data } = await api.get<EvalQuery[]>('/eval/queries', { params: { limit, offset } })
  return data
}

export async function createEvalQuery(input: {
  query: string
  description?: string
  categories?: string[]
}): Promise<EvalQuery> {
  const { data } = await api.post<EvalQuery>('/eval/queries', input)
  return data
}

export async function deleteEvalQuery(id: string): Promise<void> {
  await api.delete(`/eval/queries/${id}`)
}

export async function runEvalQuery(id: string, k = 10): Promise<EvalRun> {
  const { data } = await api.post<EvalRun>(`/eval/queries/${id}/run`, null, { params: { k } })
  return data
}

export async function listEvalRuns(evalQueryId?: string, limit = 100): Promise<EvalRun[]> {
  const { data } = await api.get<EvalRun[]>('/eval/runs', { params: { eval_query_id: evalQueryId, limit } })
  return data
}

export async function getEvalSummary(): Promise<EvalSummary> {
  const { data } = await api.get<EvalSummary>('/eval/summary')
  return data
}
