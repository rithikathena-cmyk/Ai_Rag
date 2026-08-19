import { type ChangeEvent, type DragEvent, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, FileText, FolderOpen, ServerCrash, Trash2, Upload, UploadCloud, X } from 'lucide-react'
import { deleteDocument, listDocuments, uploadDocument } from '@/api/documents'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { validateUploadFile } from '@/lib/fileValidation'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { FullPageSpinner } from '@/components/ui/Spinner'
import { StateMessage } from '@/components/ui/StateMessage'
import { cn } from '@/lib/cn'
import type { DocumentItem } from '@/types/documents'

const STATUS_TONE: Record<string, 'green' | 'amber' | 'red' | 'neutral'> = {
  ready: 'green',
  completed: 'green',
  processing: 'amber',
  pending: 'amber',
  failed: 'red',
  error: 'red',
}

const IN_PROGRESS_STATUSES = new Set(['processing', 'pending'])

interface UploadTask {
  id: string
  file: File
  progress: number
  status: 'uploading' | 'success' | 'error'
  errorMessage?: string
  controller: AbortController
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DocumentsPage() {
  const { hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selected, setSelected] = useState<DocumentItem | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [uploads, setUploads] = useState<UploadTask[]>([])
  const dragCounter = useRef(0)

  const documentsQuery = useQuery({
    queryKey: ['documents'],
    queryFn: () => listDocuments(),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      setSelected(null)
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      toast.success('Document deleted')
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't delete that document.").message),
  })

  const canUpload = hasPermission('UPLOAD_DOCUMENTS')
  const canDelete = hasPermission('DELETE_DOCUMENTS') || hasPermission('MANAGE_DOCUMENTS')

  function queueFile(file: File) {
    const validationError = validateUploadFile(file)
    if (validationError) {
      toast.error(validationError)
      return
    }

    const id = `${file.name}-${Date.now()}`
    const controller = new AbortController()
    setUploads((prev) => [...prev, { id, file, progress: 0, status: 'uploading', controller }])

    uploadDocument(
      { file },
      {
        signal: controller.signal,
        onProgress: (percent) => {
          setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, progress: percent } : u)))
        },
      },
    )
      .then(() => {
        setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, status: 'success', progress: 100 } : u)))
        void queryClient.invalidateQueries({ queryKey: ['documents'] })
        window.setTimeout(() => setUploads((prev) => prev.filter((u) => u.id !== id)), 4000)
      })
      .catch((err) => {
        if (controller.signal.aborted) {
          setUploads((prev) => prev.filter((u) => u.id !== id))
          return
        }
        const message = getApiError(err, 'Upload failed').message
        setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, status: 'error', errorMessage: message } : u)))
      })
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) queueFile(file)
  }

  function cancelUpload(task: UploadTask) {
    task.controller.abort()
  }

  function dismissUpload(id: string) {
    setUploads((prev) => prev.filter((u) => u.id !== id))
  }

  function handleDragEnter(event: DragEvent) {
    if (!canUpload) return
    event.preventDefault()
    dragCounter.current += 1
    setIsDragging(true)
  }

  function handleDragLeave(event: DragEvent) {
    if (!canUpload) return
    event.preventDefault()
    dragCounter.current -= 1
    if (dragCounter.current <= 0) {
      dragCounter.current = 0
      setIsDragging(false)
    }
  }

  function handleDrop(event: DragEvent) {
    if (!canUpload) return
    event.preventDefault()
    dragCounter.current = 0
    setIsDragging(false)
    const file = event.dataTransfer.files?.[0]
    if (file) queueFile(file)
  }

  return (
    <div>
      <PageHeader
        title="Documents"
        description="Corpus documents ingested into the knowledge base"
        actions={
          canUpload && (
            <>
              <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileChange} />
              <Button size="sm" onClick={() => fileInputRef.current?.click()}>
                <Upload className="h-4 w-4" /> Upload
              </Button>
            </>
          )
        }
      />

      {uploads.length > 0 && (
        <div className="space-y-2 px-6 pt-4">
          {uploads.map((task) => (
            <UploadCard key={task.id} task={task} onCancel={() => cancelUpload(task)} onDismiss={() => dismissUpload(task.id)} />
          ))}
        </div>
      )}

      {documentsQuery.isLoading ? (
        <FullPageSpinner />
      ) : documentsQuery.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load documents"
          description={getApiError(documentsQuery.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void documentsQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        <div className="flex">
          <div
            className="relative flex-1 overflow-x-auto p-6"
            onDragEnter={handleDragEnter}
            onDragOver={(e) => canUpload && e.preventDefault()}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            {isDragging && (
              <div className="absolute inset-3 z-10 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-accent-400 bg-accent-50/90 text-accent-700">
                <UploadCloud className="h-8 w-8" />
                <p className="text-sm font-medium">Drop a file to upload</p>
              </div>
            )}

            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="pb-2 font-medium">Filename</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 font-medium">Type</th>
                  <th className="pb-2 font-medium">Size</th>
                  <th className="pb-2 font-medium">Chunks</th>
                  {canDelete && <th className="pb-2 font-medium" />}
                </tr>
              </thead>
              <tbody>
                {documentsQuery.data?.items.map((doc) => (
                  <tr
                    key={doc.id}
                    onClick={() => setSelected(doc)}
                    className={cn(
                      'cursor-pointer border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50',
                      selected?.id === doc.id && 'bg-accent-50/60',
                    )}
                  >
                    <td className="py-2.5 pr-4 font-medium text-ink">{doc.filename}</td>
                    <td className="py-2.5 pr-4">
                      <Badge
                        tone={STATUS_TONE[doc.status] ?? 'neutral'}
                        className={cn(IN_PROGRESS_STATUSES.has(doc.status) && 'animate-pulse')}
                      >
                        {doc.status}
                      </Badge>
                    </td>
                    <td className="py-2.5 pr-4 text-neutral-600">{doc.document_type}</td>
                    <td className="py-2.5 pr-4 text-neutral-600">{formatBytes(doc.file_size_bytes)}</td>
                    <td className="py-2.5 pr-4 text-neutral-600">{doc.chunk_count}</td>
                    {canDelete && (
                      <td className="py-2.5 text-right">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            deleteMutation.mutate(doc.id)
                          }}
                          className="rounded-md p-1.5 text-neutral-400 transition-colors duration-150 hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {documentsQuery.data?.items.length === 0 && (
              <StateMessage
                icon={FolderOpen}
                title="No documents yet"
                description={canUpload ? 'Drag a file here or use Upload to add one.' : undefined}
              />
            )}
          </div>

          {selected && (
            <div className="w-80 shrink-0 animate-slide-in-right border-l border-neutral-200 p-5">
              <div className="mb-3 flex items-start justify-between">
                <h3 className="text-sm font-semibold text-ink">{selected.filename}</h3>
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="text-xs text-neutral-400 transition-colors hover:text-ink"
                >
                  Close
                </button>
              </div>
              <dl className="space-y-2 text-sm">
                <Detail label="Status" value={selected.status} />
                <Detail label="Classification" value={selected.classification ?? '—'} />
                <Detail label="Version" value={`v${selected.version_number}`} />
                <Detail label="Chunks" value={String(selected.chunk_count)} />
                <Detail label="Language" value={selected.metadata.language ?? '—'} />
                <Detail label="Pages" value={selected.metadata.page_count?.toString() ?? '—'} />
              </dl>
              {selected.summary && (
                <div className="mt-4">
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-neutral-400">Summary</p>
                  <p className="text-sm text-neutral-700">{selected.summary}</p>
                </div>
              )}
              {selected.error_message && <p className="mt-4 text-sm text-red-600">{selected.error_message}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function UploadCard({ task, onCancel, onDismiss }: { task: UploadTask; onCancel: () => void; onDismiss: () => void }) {
  return (
    <div className="animate-fade-slide-up flex items-center gap-3 rounded-xl border border-neutral-200 bg-surface p-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
        {task.status === 'success' ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
        ) : task.status === 'error' ? (
          <AlertCircle className="h-4 w-4 text-red-600" />
        ) : (
          <FileText className="h-4 w-4" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{task.file.name}</p>
        {task.status === 'uploading' && (
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
            <div
              className="h-full rounded-full bg-accent-500 transition-all duration-200 ease-out"
              style={{ width: `${task.progress}%` }}
            />
          </div>
        )}
        {task.status === 'success' && <p className="text-xs text-emerald-600">Uploaded</p>}
        {task.status === 'error' && <p className="text-xs text-red-600">{task.errorMessage}</p>}
      </div>
      <button
        type="button"
        onClick={task.status === 'uploading' ? onCancel : onDismiss}
        aria-label={task.status === 'uploading' ? 'Cancel upload' : 'Dismiss'}
        className="shrink-0 rounded-md p-1.5 text-neutral-400 transition-colors hover:bg-neutral-100 hover:text-ink"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-neutral-500">{label}</dt>
      <dd className="truncate text-right font-medium text-ink">{value}</dd>
    </div>
  )
}
