import { api } from '@/api/client'
import type { SearchRequest, SearchResponse } from '@/types/search'

export async function runSearch(body: SearchRequest): Promise<SearchResponse> {
  const { data } = await api.post<SearchResponse>('/search', body)
  return data
}
