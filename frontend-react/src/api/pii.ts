import { api } from '@/api/client'
import type { PiiOccurrenceReveal, PiiOccurrenceSummary } from '@/types/pii'

export async function listPiiOccurrences(messageId: string): Promise<{ items: PiiOccurrenceSummary[] }> {
  const { data } = await api.get<{ items: PiiOccurrenceSummary[] }>(`/admin/traces/${messageId}/pii`)
  return data
}

// Backend-authorized (Permission.PII_VIEW_RAW) and audited (PII_VIEWED) on
// every call that reaches it — see routers/pii_access.py. This function
// itself does nothing to enforce that; it is not the security boundary.
export async function revealPiiOccurrence(
  messageId: string,
  entityId: string,
  reason?: string,
): Promise<PiiOccurrenceReveal> {
  const { data } = await api.get<PiiOccurrenceReveal>(`/admin/traces/${messageId}/pii/${entityId}`, {
    params: reason ? { reason } : undefined,
  })
  return data
}
