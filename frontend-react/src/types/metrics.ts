export interface LatencySummary {
  endpoint: string
  count: number
  avg_ms: number
  p95_ms: number
}

export interface TokenUsageSummary {
  source: string
  model: string
  total_input_tokens: number
  total_output_tokens: number
  call_count: number
}

export interface MetricsResponse {
  latency_samples: Record<string, unknown>[]
  latency_summary: LatencySummary[]
  token_usage_samples: Record<string, unknown>[]
  token_usage_summary: TokenUsageSummary[]
}

export interface GatewayUsageSample {
  id: string
  request_id: string
  agent_name: string
  model: string
  tier: string
  tokens_input: number
  tokens_output: number
  latency_ms: number
  cost_usd: number
  user_id: string | null
  user_email: string | null
  role: string | null
  department: string | null
  decision: string
  denial_reason: string | null
  requested_capability: string | null
  tool_calls: string[] | null
  documents_retrieved: string[] | null
  created_at: string
}

export interface GatewayUsageSummaryRow {
  agent_name: string
  model: string
  tier: string
  call_count: number
  total_tokens_input: number
  total_tokens_output: number
  total_cost_usd: number
  avg_latency_ms: number
}

export interface GatewayUsageResponse {
  samples: GatewayUsageSample[]
  summary: GatewayUsageSummaryRow[]
  total_cost_usd: number
  denied_count: number
}

export interface GuardrailEventSample {
  direction: string
  check: string
  action: string
  detail: string
  created_at: number
}

export interface GuardrailCheckSummary {
  direction: string
  check: string
  pass_count: number
  redact_count: number
  block_count: number
}

export interface GuardrailAnalyticsResponse {
  events: GuardrailEventSample[]
  summary: GuardrailCheckSummary[]
}
