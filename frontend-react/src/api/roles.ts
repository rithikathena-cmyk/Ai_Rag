import { api } from '@/api/client'
import type { RolesResponse } from '@/types/roles'

export async function listRoles(): Promise<RolesResponse> {
  const { data } = await api.get<RolesResponse>('/admin/roles')
  return data
}
