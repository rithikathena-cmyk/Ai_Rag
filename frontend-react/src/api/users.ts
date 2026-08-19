import { api } from '@/api/client'
import type {
  TokenLimitInput, UsageInfo, UserCreateInput, UserDocumentAccess, UserItem, UserUpdateInput,
} from '@/types/users'

export async function listUsers(limit = 50, offset = 0): Promise<UserItem[]> {
  const { data } = await api.get<UserItem[]>('/users', { params: { limit, offset } })
  return data
}

export async function createUser(input: UserCreateInput): Promise<UserItem> {
  const { data } = await api.post<UserItem>('/users', input)
  return data
}

export async function updateUser(userId: string, input: UserUpdateInput): Promise<UserItem> {
  const { data } = await api.patch<UserItem>(`/users/${userId}`, input)
  return data
}

export async function getUserUsage(userId: string): Promise<UsageInfo> {
  const { data } = await api.get<UsageInfo>(`/users/${userId}/usage`)
  return data
}

export async function getMyUsage(): Promise<UsageInfo> {
  const { data } = await api.get<UsageInfo>('/users/me/usage')
  return data
}

export async function setUserTokenLimit(userId: string, input: TokenLimitInput): Promise<UserItem> {
  const { data } = await api.put<UserItem>(`/users/${userId}/token-limit`, input)
  return data
}

export async function resetUserUsage(userId: string): Promise<UsageInfo> {
  const { data } = await api.post<UsageInfo>(`/users/${userId}/usage/reset`)
  return data
}

export async function getUserDocumentAccess(userId: string): Promise<UserDocumentAccess> {
  const { data } = await api.get<UserDocumentAccess>(`/users/${userId}/document-access`)
  return data
}

export async function getMyPreferences(userId: string): Promise<Record<string, string>> {
  const { data } = await api.get<Record<string, string>>(`/users/${userId}/preferences`)
  return data
}

export async function updateMyPreferences(
  userId: string,
  updates: Record<string, string>,
): Promise<Record<string, string>> {
  const { data } = await api.put<Record<string, string>>(`/users/${userId}/preferences`, updates)
  return data
}
