import { type ChangeEvent, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowDown,
  Check,
  CheckCircle2,
  Copy,
  Paperclip,
  RotateCw,
  Send,
  ShieldAlert,
  ThumbsDown,
  ThumbsUp,
  X,
} from 'lucide-react'
import { getConversation, sendChatMessage } from '@/api/chat'
import { uploadDocument } from '@/api/documents'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { isBlockedResponse } from '@/lib/guardrails'
import { validateUploadFile } from '@/lib/fileValidation'
import { formatTimestamp } from '@/lib/formatTime'
import { toast } from '@/lib/toast'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Markdown } from '@/components/chat/Markdown'
import { GuardrailsStatus } from '@/components/chat/GuardrailsStatus'
import { ModelSelector } from '@/components/chat/ModelSelector'
import { SourceModal } from '@/components/chat/SourceModal'
import { SuggestedPrompts } from '@/components/chat/SuggestedPrompts'
import { PendingIndicator } from '@/components/chat/PendingIndicator'
import { cn } from '@/lib/cn'
import type { ChatSource, ChatThreadMessage, Confidence, ModelTier } from '@/types/chat'

const CONFIDENCE_TONE: Record<Confidence, 'green' | 'amber' | 'red' | 'neutral'> = {
  high: 'green',
  medium: 'amber',
  low: 'red',
  'n/a': 'neutral',
}

const TEXTAREA_MAX_HEIGHT = 160
const SCROLL_BOTTOM_THRESHOLD = 96

interface AttachmentTask {
  id: string
  file: File
  progress: number
  status: 'uploading' | 'success' | 'error'
  errorMessage?: string
  controller: AbortController
}

export function ChatPage() {
  const { user, capabilities, hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [conversationId, setConversationId] = useState<string | undefined>(undefined)
  const [messages, setMessages] = useState<ChatThreadMessage[]>([])
  const [draft, setDraft] = useState('')
  const [selectedSource, setSelectedSource] = useState<ChatSource | null>(null)
  const [userSelectedTier, setUserSelectedTier] = useState<ModelTier | null>(null)
  const [lastResolvedTier, setLastResolvedTier] = useState<string | null>(null)
  const [showScrollButton, setShowScrollButton] = useState(false)
  const [attachments, setAttachments] = useState<AttachmentTask[]>([])

  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const canAttach = hasPermission('UPLOAD_DOCUMENTS')

  const sendMutation = useMutation({
    mutationFn: sendChatMessage,
    onSuccess: (data) => {
      setConversationId(data.conversation_id)
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('conversation', data.conversation_id)
        return next
      })
      setLastResolvedTier(data.model_tier)
      setMessages((prev) => [
        ...prev.slice(0, -1),
        {
          id: `${data.conversation_id}-assistant-${prev.length}-${Date.now()}`,
          role: 'assistant',
          content: data.reply,
          createdAt: Date.now(),
          sources: data.sources,
          report: data.report,
          confidence: data.confidence,
          trace: data.trace,
          modelTier: data.model_tier,
          responseTimeMs: data.response_time_ms,
          blocked: isBlockedResponse(data.trace),
          degraded: data.degraded,
          degradedReason: data.degraded_reason,
        },
      ])
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
    onError: (err) => {
      const apiError = getApiError(err, 'Something went wrong. Please try again.')
      setMessages((prev) => [
        ...prev.slice(0, -1),
        {
          id: `error-${prev.length}-${Date.now()}`,
          role: 'assistant',
          content: apiError.message,
          createdAt: Date.now(),
          failed: true,
        },
      ])
    },
  })

  function handleScroll() {
    const el = scrollContainerRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setShowScrollButton(distanceFromBottom > SCROLL_BOTTOM_THRESHOLD)
  }

  function scrollToBottom(behavior: ScrollBehavior = 'smooth') {
    messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' })
  }

  useEffect(() => {
    if (!showScrollButton) scrollToBottom()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages])

  function resizeTextarea() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`
  }

  function handleDraftChange(value: string) {
    setDraft(value)
    requestAnimationFrame(resizeTextarea)
  }

  async function openConversation(id: string) {
    setConversationId(id)
    try {
      const detail = await getConversation(id)
      setMessages(
        detail.messages.map((m) => ({
          id: m.id,
          role: m.role === 'user' ? 'user' : 'assistant',
          content: m.content,
          createdAt: new Date(m.created_at).getTime(),
          sources: m.sources ?? undefined,
          report: m.report,
          trace: m.trace ?? undefined,
          blocked: m.trace ? isBlockedResponse(m.trace) : undefined,
        })),
      )
    } catch (err) {
      toast.error(getApiError(err, "Couldn't load that conversation.").message)
    }
  }

  // Single source of truth for "which conversation is open" is the URL — the
  // Sidebar's "New chat" link and its recent-conversation links both just
  // navigate to "/" or "/?conversation=<id>", and this effect reacts to that.
  useEffect(() => {
    const id = searchParams.get('conversation')
    if (id) {
      if (id !== conversationId) void openConversation(id)
    } else if (conversationId) {
      setConversationId(undefined)
      setMessages([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  function dispatchMessage(messageText: string) {
    const message = messageText.trim()
    if (!message || sendMutation.isPending) return

    setMessages((prev) => [
      ...prev,
      { id: `user-${prev.length}-${Date.now()}`, role: 'user', content: message, createdAt: Date.now() },
      { id: `pending-${prev.length}-${Date.now()}`, role: 'assistant', content: '', createdAt: Date.now(), pending: true },
    ])
    sendMutation.mutate({
      message,
      conversation_id: conversationId,
      model_tier: userSelectedTier ?? undefined,
    })
  }

  function handleSend() {
    if (!draft.trim() || sendMutation.isPending) return
    dispatchMessage(draft)
    setDraft('')
    requestAnimationFrame(resizeTextarea)
  }

  function handleSuggestion(prompt: string) {
    dispatchMessage(prompt)
  }

  function handleRegenerate(assistantMessage: ChatThreadMessage) {
    const idx = messages.findIndex((m) => m.id === assistantMessage.id)
    const precedingUser = [...messages.slice(0, idx)].reverse().find((m) => m.role === 'user')
    if (!precedingUser || sendMutation.isPending) return
    setMessages((prev) => [
      ...prev.filter((m) => m.id !== assistantMessage.id),
      {
        id: `pending-regenerate-${Date.now()}`,
        role: 'assistant',
        content: '',
        createdAt: Date.now(),
        pending: true,
      },
    ])
    sendMutation.mutate({
      message: precedingUser.content,
      conversation_id: conversationId,
      model_tier: userSelectedTier ?? undefined,
    })
  }

  function queueAttachment(file: File) {
    const validationError = validateUploadFile(file)
    if (validationError) {
      toast.error(validationError)
      return
    }

    const id = `${file.name}-${Date.now()}`
    const controller = new AbortController()
    setAttachments((prev) => [...prev, { id, file, progress: 0, status: 'uploading', controller }])

    uploadDocument(
      { file },
      {
        signal: controller.signal,
        onProgress: (percent) => {
          setAttachments((prev) => prev.map((a) => (a.id === id ? { ...a, progress: percent } : a)))
        },
      },
    )
      .then(() => {
        setAttachments((prev) => prev.map((a) => (a.id === id ? { ...a, status: 'success', progress: 100 } : a)))
        void queryClient.invalidateQueries({ queryKey: ['documents'] })
        window.setTimeout(() => setAttachments((prev) => prev.filter((a) => a.id !== id)), 5000)
      })
      .catch((err) => {
        if (controller.signal.aborted) {
          setAttachments((prev) => prev.filter((a) => a.id !== id))
          return
        }
        const message = getApiError(err, 'Upload failed').message
        setAttachments((prev) => prev.map((a) => (a.id === id ? { ...a, status: 'error', errorMessage: message } : a)))
      })
  }

  function handleAttachmentChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) queueAttachment(file)
  }

  const allowedTiers = capabilities?.model_tiers_allowed ?? []
  const currentTier = (userSelectedTier ?? lastResolvedTier ?? capabilities?.default_model_tier ?? 'haiku') as ModelTier

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="relative flex-1 overflow-y-auto" ref={scrollContainerRef} onScroll={handleScroll}>
          {messages.length === 0 ? (
            <WelcomeState
              displayName={user?.display_name ?? null}
              roleLabel={capabilities?.display_name ?? user?.role ?? ''}
              departments={capabilities?.knowledge_departments ?? []}
              onSelectSuggestion={handleSuggestion}
            />
          ) : (
            <div className="mx-auto max-w-2xl space-y-7 px-4 py-6 sm:px-6">
              {messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  onSelectSource={setSelectedSource}
                  onRegenerate={() => handleRegenerate(m)}
                  regenerating={sendMutation.isPending}
                />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}

          {showScrollButton && (
            <button
              type="button"
              onClick={() => scrollToBottom()}
              className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-neutral-200 bg-surface px-3 py-1.5 text-xs font-medium text-neutral-600 shadow-md transition-colors hover:bg-neutral-50"
            >
              <ArrowDown className="h-3.5 w-3.5" /> Latest message
            </button>
          )}
        </div>

        <div className="border-t border-neutral-200 p-4">
          <div className="mx-auto max-w-2xl space-y-2">
            {attachments.length > 0 && (
              <div className="space-y-1.5">
                {attachments.map((task) => (
                  <AttachmentCard
                    key={task.id}
                    task={task}
                    onCancel={() => task.controller.abort()}
                    onDismiss={() => setAttachments((prev) => prev.filter((a) => a.id !== task.id))}
                  />
                ))}
              </div>
            )}
            <div className="flex items-end gap-2">
              {canAttach && (
                <>
                  <input ref={fileInputRef} type="file" className="hidden" onChange={handleAttachmentChange} />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    aria-label="Attach a document to the knowledge base"
                    title="Attach a document to the knowledge base"
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-neutral-300 text-neutral-500 transition-colors duration-150 hover:bg-neutral-100 hover:text-ink"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                </>
              )}
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => handleDraftChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleSend()
                  }
                }}
                rows={1}
                placeholder="Message ATHENA..."
                aria-label="Message"
                className="max-h-40 flex-1 resize-none overflow-y-auto rounded-xl border border-neutral-300 bg-surface px-3.5 py-2.5 text-sm text-neutral-900 transition-shadow duration-150 placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-accent-400"
              />
              <Button
                size="md"
                onClick={handleSend}
                loading={sendMutation.isPending}
                disabled={!draft.trim()}
                aria-label="Send message"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex items-center justify-between">
              {allowedTiers.length > 0 && (
                <ModelSelector value={currentTier} onChange={setUserSelectedTier} allowedTiers={allowedTiers} />
              )}
              <p className="text-xs text-neutral-400">Enter to send · Shift+Enter for a new line</p>
            </div>
          </div>
        </div>
      </div>

      <SourceModal source={selectedSource} onClose={() => setSelectedSource(null)} />
    </div>
  )
}

function AttachmentCard({ task, onCancel, onDismiss }: { task: AttachmentTask; onCancel: () => void; onDismiss: () => void }) {
  return (
    <div className="animate-fade-slide-up flex items-center gap-3 rounded-xl border border-neutral-200 bg-surface p-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
        {task.status === 'success' ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
        ) : task.status === 'error' ? (
          <AlertCircle className="h-4 w-4 text-red-600" />
        ) : (
          <Paperclip className="h-4 w-4" />
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
        {task.status === 'success' && <p className="text-xs text-emerald-600">Ready for analysis</p>}
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

function WelcomeState({
  displayName,
  roleLabel,
  departments,
  onSelectSuggestion,
}: {
  displayName: string | null
  roleLabel: string
  departments: string[]
  onSelectSuggestion: (prompt: string) => void
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-4 py-10">
      <div className="w-full max-w-xl text-center">
        <div className="animate-fade-slide-up mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accent-600 text-xl font-semibold text-white">
          A
        </div>
        <h1 className="animate-fade-slide-up text-xl font-semibold text-ink">
          {displayName ? `Welcome back, ${displayName}` : 'How can I help you today?'}
        </h1>
        <p className="animate-fade-slide-up mt-1.5 text-sm text-neutral-500">
          Signed in as <span className="font-medium text-neutral-700">{roleLabel}</span> — ask me anything about
          the knowledge base you have access to.
        </p>

        <div className="mt-8">
          <SuggestedPrompts departments={departments} onSelect={onSelectSuggestion} />
        </div>
      </div>
    </div>
  )
}

function MessageBubble({
  message,
  onSelectSource,
  onRegenerate,
  regenerating,
}: {
  message: ChatThreadMessage
  onSelectSource: (source: ChatSource) => void
  onRegenerate: () => void
  regenerating: boolean
}) {
  const isUser = message.role === 'user'
  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  function handleFeedback(tone: 'up' | 'down') {
    setFeedback((prev) => (prev === tone ? null : tone))
    if (feedback !== tone) toast.success('Thanks for your feedback')
  }

  return (
    <div className={cn('group/message flex animate-fade-slide-up flex-col gap-1.5', isUser && 'items-end')}>
      <div className={cn('max-w-full text-sm', isUser && 'max-w-[75%] rounded-2xl bg-accent-50 px-4 py-2.5 text-ink')}>
        {message.pending ? (
          <PendingIndicator />
        ) : isUser ? (
          <span className="whitespace-pre-wrap">{message.content}</span>
        ) : message.failed ? (
          <div className="flex items-start gap-2 text-red-600">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{message.content}</span>
          </div>
        ) : message.blocked ? (
          <div className="flex items-start gap-2">
            <ShieldAlert className="mt-1 h-4 w-4 shrink-0 text-neutral-400" />
            <Markdown content={message.content} className="min-w-0 flex-1 text-neutral-600" />
          </div>
        ) : (
          <Markdown content={message.content} sources={message.sources} onCitationClick={onSelectSource} />
        )}
      </div>

      <div className="flex items-center gap-1.5 text-xs text-neutral-400">
        {message.createdAt && !message.pending && <span>{formatTimestamp(message.createdAt)}</span>}
        {!isUser && message.modelTier && !message.pending && (
          <span className="capitalize">· {message.modelTier}</span>
        )}
      </div>

      {!isUser && message.degraded && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          This reply used a fallback search instead of the AI model
          {message.degradedReason ? ` (${message.degradedReason.replaceAll('_', ' ')})` : ''}. Try again or pick a
          different model above for your next message.
        </div>
      )}

      {!isUser && !message.pending && message.content && (
        <div className="flex items-center gap-3 opacity-0 transition-opacity group-hover/message:opacity-100">
          <button
            type="button"
            onClick={() => void handleCopy()}
            className="flex items-center gap-1 text-xs text-neutral-400 transition-colors hover:text-neutral-700"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            {copied ? 'Copied' : 'Copy'}
          </button>
          {!message.failed && (
            <button
              type="button"
              onClick={onRegenerate}
              disabled={regenerating}
              className="flex items-center gap-1 text-xs text-neutral-400 transition-colors hover:text-neutral-700 disabled:opacity-50"
            >
              <RotateCw className="h-3 w-3" /> Regenerate
            </button>
          )}
          {!message.failed && !message.blocked && (
            <span className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => handleFeedback('up')}
                aria-label="Good response"
                aria-pressed={feedback === 'up'}
                className={cn(
                  'text-neutral-400 transition-colors hover:text-emerald-600',
                  feedback === 'up' && 'text-emerald-600',
                )}
              >
                <ThumbsUp className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => handleFeedback('down')}
                aria-label="Bad response"
                aria-pressed={feedback === 'down'}
                className={cn(
                  'text-neutral-400 transition-colors hover:text-red-600',
                  feedback === 'down' && 'text-red-600',
                )}
              >
                <ThumbsDown className="h-3 w-3" />
              </button>
            </span>
          )}
          {message.trace && message.trace.length > 0 && (
            <GuardrailsStatus trace={message.trace} responseTimeMs={message.responseTimeMs} />
          )}
        </div>
      )}

      {!isUser && message.confidence && (
        <Badge tone={CONFIDENCE_TONE[message.confidence]} className="self-start">
          {message.confidence} confidence
        </Badge>
      )}

      {!isUser && message.sources && message.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.sources.map((s) => (
            <button
              key={`${s.index}-${s.chunk_id}`}
              type="button"
              onClick={() => onSelectSource(s)}
              className="rounded-md border border-neutral-200 bg-surface px-2 py-1 text-xs text-neutral-600 transition-colors duration-150 hover:border-accent-300 hover:text-ink"
            >
              [{s.index}] {s.document_filename ?? s.document_id}
            </button>
          ))}
        </div>
      )}

      {!isUser && message.report && (
        <a
          href={message.report.download_url}
          className="text-xs font-medium text-accent-700 hover:underline"
          target="_blank"
          rel="noreferrer"
        >
          Download report: {message.report.title}
        </a>
      )}
    </div>
  )
}
