export interface UploadLogItem {
  id: string
  document_id: string | null
  filename: string | null
  content_type: string | null
  file_size_bytes: number | null
  outcome: string
  error_code: string | null
  error_message: string | null
  created_at: string
}

export interface UploadLogListResponse {
  items: UploadLogItem[]
  total: number
}

export interface AuditEvent {
  event_id: string
  event_type: string
  actor_id: string | null
  actor_email: string | null
  actor_role: string | null
  resource_type: string | null
  resource_id: string | null
  action: string | null
  outcome: string
  reason_code: string | null
  request_id: string
  session_id: string | null
  metadata: Record<string, string | number | boolean | null>
  created_at: string
}

export interface AuditEventListResponse {
  items: AuditEvent[]
  total: number
}

export interface AuditEventFilters {
  event_type?: string
  outcome?: string
  request_id?: string
  actor_id?: string
  resource_type?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}
