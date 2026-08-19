import { api } from '@/api/client'

export interface HealthStatus {
  status: string
  qdrant: string
  postgres: string
}

export async function getHealth(): Promise<HealthStatus> {
  const { data } = await api.get<HealthStatus>('/health')
  return data
}
