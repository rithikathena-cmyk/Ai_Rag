import { api } from '@/api/client'
import type { ChatRequest, ChatResponse, ConversationDetailResponse, ConversationListResponse, ConversationSummary } from '@/types/chat'

export async function sendChatMessage(body: ChatRequest): Promise<ChatResponse> {
  const { data } = await api.post<ChatResponse>('/chat', body)
  return data
}

export async function listConversations(limit = 50, offset = 0): Promise<ConversationListResponse> {
  const { data } = await api.get<ConversationListResponse>('/conversations', { params: { limit, offset } })
  return data
}

export async function getConversation(conversationId: string): Promise<ConversationDetailResponse> {
  const { data } = await api.get<ConversationDetailResponse>(`/conversations/${conversationId}`)
  return data
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await api.delete(`/conversations/${conversationId}`)
}

export async function updateConversation(
  conversationId: string,
  updates: { title?: string; pinned?: boolean },
): Promise<ConversationSummary> {
  const { data } = await api.patch<ConversationSummary>(`/conversations/${conversationId}`, updates)
  return data
}
