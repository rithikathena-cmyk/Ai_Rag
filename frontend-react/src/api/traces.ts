import { api } from '@/api/client'
import type { TraceFilters, TraceListResponse } from '@/types/traces'

export async function listTraces(filters: TraceFilters = {}): Promise<TraceListResponse> {
  const { data } = await api.get<TraceListResponse>('/traces', { params: filters })
  return data
}
