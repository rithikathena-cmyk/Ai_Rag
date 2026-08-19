export type Role =
  | 'admin'
  | 'user'
  | 'hr'
  | 'project_manager'
  | 'ceo'
  | 'plant_manager'
  | 'production_manager'
  | 'production_supervisor'
  | 'operator'
  | 'maintenance_engineer'
  | 'maintenance_manager'
  | 'quality_engineer'
  | 'warehouse_staff'
  | 'inventory_manager'
  | 'procurement_officer'
  | 'planner'

export type Permission =
  | 'CHAT'
  | 'VIEW_CONVERSATIONS'
  | 'VIEW_DOCUMENTS'
  | 'UPLOAD_DOCUMENTS'
  | 'DELETE_DOCUMENTS'
  | 'MANAGE_DOCUMENTS'
  | 'VIEW_ANALYTICS'
  | 'VIEW_USERS'
  | 'MANAGE_USERS'
  | 'VIEW_ROLES'
  | 'MANAGE_ROLES'
  | 'VIEW_AUDIT_LOGS'
  | 'SYSTEM_SETTINGS'
  | 'MANAGE_EMPLOYEE_PII'
  | 'MANAGE_GUARDRAIL_POLICIES'
  | 'POLICY_READ'
  | 'POLICY_SIMULATE'
  | 'POLICY_PROPOSE'
  | 'POLICY_APPROVE'
  | 'PII_VIEW_RAW'

export interface CurrentUser {
  id: string
  email: string
  display_name: string | null
  role: Role
  is_active: boolean
  created_at: string
}

export interface Capabilities {
  role: Role
  display_name: string
  model_tiers_allowed: string[]
  default_model_tier: string
  escalate_to_opus_for: string[]
  tools: string[]
  knowledge_departments: string[]
  capabilities: string[]
  all_capabilities: boolean
  granted_permissions: Permission[]
  all_permissions: boolean
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface AccessTokenResponse {
  access_token: string
  token_type: string
}

export interface DemoUserTile {
  demo_role: string
  display_name: string
  description: string
  is_privileged: boolean
  email: string
}

export interface DemoUsersResponse {
  enabled: boolean
  users: DemoUserTile[]
}
