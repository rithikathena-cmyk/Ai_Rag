import { api } from '@/api/client'
import type { AuditEventFilters, AuditEventListResponse, UploadLogListResponse } from '@/types/auditLogs'

export async function listUploadLogs(
  outcome?: string,
  limit = 50,
  offset = 0,
): Promise<UploadLogListResponse> {
  const { data } = await api.get<UploadLogListResponse>('/upload-logs', { params: { outcome, limit, offset } })
  return data
}

export async function listAuditEvents(filters: AuditEventFilters = {}): Promise<AuditEventListResponse> {
  const { data } = await api.get<AuditEventListResponse>('/audit/events', { params: filters })
  return data
}
