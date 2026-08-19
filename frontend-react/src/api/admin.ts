import { api } from '@/api/client'
import type {
  ApprovalItem,
  ApprovalListResponse,
  CollectionItem,
  DecideApprovalInput,
  IndexConsistencyResponse,
  ModelAvailability,
} from '@/types/admin'

export async function listApprovals(status = 'pending', limit = 50, offset = 0): Promise<ApprovalListResponse> {
  const { data } = await api.get<ApprovalListResponse>('/approvals', { params: { status, limit, offset } })
  return data
}

export async function decideApproval(id: string, input: DecideApprovalInput): Promise<ApprovalItem> {
  const { data } = await api.post<ApprovalItem>(`/approvals/${id}/decide`, input)
  return data
}

export async function listCollections(): Promise<CollectionItem[]> {
  const { data } = await api.get<CollectionItem[]>('/admin/collections')
  return data
}

export async function getIndexConsistency(): Promise<IndexConsistencyResponse> {
  const { data } = await api.get<IndexConsistencyResponse>('/admin/index-consistency')
  return data
}

export async function getModelAvailability(): Promise<ModelAvailability> {
  const { data } = await api.get<ModelAvailability>('/admin/model-availability')
  return data
}

export async function setModelAvailability(disabled: boolean): Promise<ModelAvailability> {
  const { data } = await api.put<ModelAvailability>('/admin/model-availability', { disabled })
  return data
}
