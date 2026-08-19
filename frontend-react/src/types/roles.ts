export interface RoleSummary {
  role: string
  display_name: string
  department_default: string | null
  tiers_allowed: string[]
  knowledge_departments: string[]
  tools: string[]
  granted_permissions: string[]
  all_permissions: boolean
  quotas: Record<string, unknown>
}

export interface RolesResponse {
  roles: RoleSummary[]
}
