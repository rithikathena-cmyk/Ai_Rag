import type { Role } from '@/types/auth'

export interface UserItem {
  id: string
  email: string
  display_name: string | null
  is_active: boolean
  role: Role
  department: string | null
  created_at: string
  daily_token_limit_override: number | null
  monthly_token_limit_override: number | null
}

export interface UsageInfo {
  role: string
  daily_requests_limit: number | null
  daily_requests_used: number
  daily_tokens_limit: number | null
  daily_tokens_used: number
  monthly_tokens_limit: number | null
  monthly_tokens_used: number
  monthly_cost_usd_limit: number | null
  monthly_cost_usd_used: number
  requests_per_minute_limit: number | null
  max_concurrent_requests_limit: number | null
}

export interface UserCreateInput {
  email: string
  display_name?: string
  password: string
  role?: string
  department?: string
}

export interface UserUpdateInput {
  role?: string
  department?: string
  is_active?: boolean
}

export interface TokenLimitInput {
  daily_tokens?: number | null
  monthly_tokens?: number | null
}

export interface UserDocumentAccessItem {
  id: string
  title: string
  department: string | null
  security_classification: string
}

export interface UserDocumentAccess {
  role: string
  department: string | null
  /** null = the LLM-RBAC kill switch is off — no department restriction at all. */
  knowledge_departments: string[] | null
  can_view: boolean
  can_upload: boolean
  can_delete: boolean
  can_manage: boolean
  total_visible: number
  documents: UserDocumentAccessItem[]
}
