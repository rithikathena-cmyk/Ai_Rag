import type { ChatTraceStep } from '@/types/chat'

export interface TraceListItem {
  message_id: string
  conversation_id: string
  user_id: string | null
  user_email: string | null
  user_display_name: string | null
  role: string | null
  department: string | null
  question: string | null
  created_at: string
  trace: ChatTraceStep[]
}

export interface TraceListResponse {
  items: TraceListItem[]
  total: number
}

export interface TraceFilters {
  role?: string
  department?: string
  blocked?: boolean
  user_id?: string
  date_from?: string
  date_to?: string
  limit?: number
  offset?: number
}
