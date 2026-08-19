import { api } from '@/api/client'
import type { DocumentItem, DocumentListResponse, UploadDocumentInput } from '@/types/documents'

export async function listDocuments(limit = 50, offset = 0): Promise<DocumentListResponse> {
  const { data } = await api.get<DocumentListResponse>('/documents', { params: { limit, offset } })
  return data
}

export async function getDocument(documentId: string): Promise<DocumentItem> {
  const { data } = await api.get<DocumentItem>(`/documents/${documentId}`)
  return data
}

export async function uploadDocument(
  input: UploadDocumentInput,
  options?: { onProgress?: (percent: number) => void; signal?: AbortSignal },
): Promise<DocumentItem> {
  const form = new FormData()
  form.append('file', input.file)
  if (input.department) form.append('department', input.department)
  if (input.project) form.append('project', input.project)
  if (input.securityClassification) form.append('security_classification', input.securityClassification)
  if (input.previousVersionOf) form.append('previous_version_of', input.previousVersionOf)

  const { data } = await api.post<DocumentItem>('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    signal: options?.signal,
    onUploadProgress: (event) => {
      if (!options?.onProgress || !event.total) return
      options.onProgress(Math.round((event.loaded / event.total) * 100))
    },
  })
  return data
}

export async function deleteDocument(documentId: string): Promise<void> {
  await api.delete(`/documents/${documentId}`)
}
