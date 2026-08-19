import { api } from '@/api/client'
import type { AccessTokenResponse, Capabilities, CurrentUser, DemoUsersResponse, TokenResponse } from '@/types/auth'

export async function login(email: string, password: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>('/auth/login', { email, password })
  return data
}

export async function listDemoUsers(): Promise<DemoUsersResponse> {
  const { data } = await api.get<DemoUsersResponse>('/auth/demo-users')
  return data
}

export async function demoLogin(demoRole: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>('/auth/demo-login', { demo_role: demoRole })
  return data
}

export async function refresh(refreshToken: string): Promise<AccessTokenResponse> {
  const { data } = await api.post<AccessTokenResponse>('/auth/refresh', { refresh_token: refreshToken })
  return data
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const { data } = await api.get<CurrentUser>('/auth/me')
  return data
}

export async function getCapabilities(): Promise<Capabilities> {
  const { data } = await api.get<Capabilities>('/users/me/capabilities')
  return data
}
