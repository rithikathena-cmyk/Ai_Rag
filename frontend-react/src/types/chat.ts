export interface ChatSource {
  index: number
  chunk_id: string
  document_id: string
  document_filename: string | null
  document_department: string | null
  document_type: string | null
  security_classification: string | null
  chunk_index: number
  text: string
}

export interface ChatReport {
  id: string
  title: string
  format: string
  row_count: number
  download_url: string
}

export interface ChatTraceStep {
  agent: string
  tool: string
  input: string | null
  summary: string
}

export type Confidence = 'high' | 'medium' | 'low' | 'n/a'

export type ModelTier = 'haiku' | 'sonnet' | 'opus'

export interface ChatResponse {
  conversation_id: string
  reply: string
  sources: ChatSource[]
  report: ChatReport | null
  trace: ChatTraceStep[]
  confidence: Confidence
  model_tier: string
  degraded: boolean
  degraded_reason: string | null
  response_time_ms: number
}

export interface ChatRequest {
  message: string
  conversation_id?: string
  top_k?: number
  action?: string
  model_tier?: ModelTier
}

export interface ConversationSummary {
  id: string
  user_id: string | null
  title: string | null
  message_count: number
  pinned_at: string | null
  created_at: string
  updated_at: string | null
}

export interface ConversationListResponse {
  items: ConversationSummary[]
  total: number
}

export interface ConversationMessage {
  id: string
  role: string
  content: string
  sources: ChatSource[] | null
  report: ChatReport | null
  trace: ChatTraceStep[] | null
  created_at: string
}

export interface ConversationDetailResponse extends ConversationSummary {
  summary: string | null
  messages: ConversationMessage[]
}

export interface ChatThreadMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: number
  sources?: ChatSource[]
  report?: ChatReport | null
  confidence?: Confidence
  trace?: ChatTraceStep[]
  modelTier?: string
  responseTimeMs?: number
  blocked?: boolean
  degraded?: boolean
  degradedReason?: string | null
  pending?: boolean
  failed?: boolean
}
