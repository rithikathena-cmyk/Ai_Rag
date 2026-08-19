import { api } from '@/api/client'
import type { GatewayUsageResponse, GuardrailAnalyticsResponse, MetricsResponse } from '@/types/metrics'

export async function getMetrics(): Promise<MetricsResponse> {
  const { data } = await api.get<MetricsResponse>('/admin/metrics')
  return data
}

export async function getGatewayUsage(limit = 200, decision?: string): Promise<GatewayUsageResponse> {
  const { data } = await api.get<GatewayUsageResponse>('/admin/gateway-usage', { params: { limit, decision } })
  return data
}

export async function getGuardrailAnalytics(): Promise<GuardrailAnalyticsResponse> {
  const { data } = await api.get<GuardrailAnalyticsResponse>('/admin/guardrail-analytics')
  return data
}
