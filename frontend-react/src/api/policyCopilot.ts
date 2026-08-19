import { api } from '@/api/client'
import type {
  CopilotChatResponse,
  CopilotPolicyRow,
  CopilotProposal,
} from '@/types/policyCopilot'

export async function sendCopilotMessage(message: string): Promise<CopilotChatResponse> {
  const { data } = await api.post<CopilotChatResponse>('/policy-copilot/chat', { message })
  return data
}

export async function listCopilotPolicies(): Promise<{ policies: CopilotPolicyRow[] }> {
  const { data } = await api.get<{ policies: CopilotPolicyRow[] }>('/policy-copilot/policies')
  return data
}

export async function listCopilotProposals(status?: string): Promise<{ items: CopilotProposal[] }> {
  const { data } = await api.get<{ items: CopilotProposal[] }>('/policy-copilot/proposals', {
    params: status ? { status } : undefined,
  })
  return data
}

export async function approveCopilotProposal(id: string, reason?: string): Promise<{ applied: unknown[] }> {
  const { data } = await api.post(`/policy-copilot/proposals/${id}/approve`, { reason: reason ?? null })
  return data
}

export async function rejectCopilotProposal(id: string, reason?: string): Promise<void> {
  await api.post(`/policy-copilot/proposals/${id}/reject`, { reason: reason ?? null })
}
