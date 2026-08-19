export interface ApprovalItem {
  id: string
  action: string
  target_type: string
  target_id: string
  requested_by: string | null
  requested_by_email: string | null
  role: string | null
  status: string
  decided_by: string | null
  decided_by_email: string | null
  decided_at: string | null
  reason: string | null
  created_at: string
  payload: Record<string, unknown> | null
}

export interface ApprovalListResponse {
  items: ApprovalItem[]
  total: number
}

export interface DecideApprovalInput {
  decision: 'approved' | 'rejected'
  reason?: string
  values?: Record<string, string>
}

export interface CollectionItem {
  name: string
  points_count: number | null
  status: string
  is_primary: boolean
}

export interface IndexConsistencyItem {
  document_id: string
  filename: string
  postgres_chunk_count: number
  qdrant_point_count: number
}

export interface IndexConsistencyResponse {
  checked: number
  inconsistent: IndexConsistencyItem[]
}

export interface ModelAvailability {
  disabled: boolean
}
