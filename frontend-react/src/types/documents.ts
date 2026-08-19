export interface DocumentMetadata {
  title: string | null
  author: string | null
  creation_date: string | null
  modified_date: string | null
  language: string | null
  page_count: number | null
  headings: string[]
  keywords: string[]
  table_count: number
  image_count: number
}

export interface DocumentItem {
  id: string
  filename: string
  document_type: string
  file_size_bytes: number
  status: string
  error_message: string | null
  classification: string | null
  classification_confidence: number | null
  classification_method: string | null
  chunk_count: number
  summary: string | null
  lineage_id: string
  version_number: number
  previous_version_id: string | null
  is_latest_version: boolean
  metadata: DocumentMetadata
  created_at: string
}

export interface DocumentListResponse {
  items: DocumentItem[]
  total: number
}

export interface UploadDocumentInput {
  file: File
  department?: string
  project?: string
  securityClassification?: string
  previousVersionOf?: string
}
