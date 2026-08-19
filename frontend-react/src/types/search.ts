export type SearchMode = 'hybrid' | 'semantic' | 'keyword'

export interface SearchFilters {
  document_id?: string
  document_ids?: string[]
  document_type?: string
  classification?: string
  language?: string
  latest_version_only?: boolean
}

export interface SearchRequest {
  query: string
  mode: SearchMode
  top_k: number
  rerank: boolean
  filters?: SearchFilters
}

export interface SearchResultItem {
  chunk_id: string
  document_id: string
  document_filename: string | null
  chunk_index: number
  parent_chunk_id: string | null
  text: string
  strategy: string
  score: number
}

export interface SearchResponse {
  query: string
  mode: string
  total: number
  reranked: boolean
  results: SearchResultItem[]
}
