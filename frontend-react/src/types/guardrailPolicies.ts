export type GuardrailPolicyCategory = 'PII' | 'REGEX' | 'WORD_FILTER' | 'SEMANTIC' | 'PROMPT_INJECTION' | 'MESSAGE_LIMIT'
export type GuardrailPolicyAction = 'ALLOW' | 'FLAG' | 'MASK' | 'REDACT' | 'BLOCK' | 'ESCALATE'
export type GuardrailPolicyMode = 'ENFORCE' | 'DRY_RUN'
export type PIIDetectionSource = 'regex' | 'presidio' | 'gliner'

export interface GuardrailPolicy {
  id: string
  policy_key: string
  name: string
  description: string | null
  category: string
  enabled: boolean
  action: string
  priority: number
  configuration: Record<string, unknown>
  mode: GuardrailPolicyMode
  version: number
  created_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string | null
}

export interface GuardrailPolicyListResponse {
  items: GuardrailPolicy[]
  total: number
}

export interface GuardrailPolicyCreateInput {
  policy_key: string
  name: string
  description?: string
  category: string
  action: string
  priority?: number
  configuration: Record<string, unknown>
  mode?: GuardrailPolicyMode
}

export interface GuardrailPolicyUpdateInput {
  expected_version: number
  name?: string
  description?: string
  enabled?: boolean
  action?: string
  priority?: number
  configuration?: Record<string, unknown>
  mode?: GuardrailPolicyMode
  reason?: string
}

export interface GuardrailPolicyUpdateResponse {
  status: 'applied' | 'pending_approval'
  policy: GuardrailPolicy | null
  approval_id: string | null
}

export interface GuardrailPolicyVersion {
  version: number
  changed_by: string | null
  previous_configuration: Record<string, unknown> | null
  new_configuration: Record<string, unknown>
  reason: string | null
  changed_at: string
}

export interface GuardrailPolicyTestInput {
  category: string
  configuration: Record<string, unknown>
  action?: string
  sample_text: string
  direction?: 'input' | 'output'
}

export interface GuardrailPolicyTestResult {
  category: string
  detected: boolean
  action: string
  risk_level: string
  detail: string
}
