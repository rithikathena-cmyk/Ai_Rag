import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, MessageSquare, MessagesSquare, Pencil, Pin, PinOff, Plus, ServerCrash, Trash2, X } from 'lucide-react'
import { deleteConversation, listConversations, updateConversation } from '@/api/chat'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { groupByDate, splitPinned } from '@/lib/conversationGroups'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { cn } from '@/lib/cn'
import type { ConversationSummary } from '@/types/chat'

function formatFullDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function ChatHistoryPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['conversations'],
    queryFn: () => listConversations(200),
  })

  const pinMutation = useMutation({
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => updateConversation(id, { pinned }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['conversations'] }),
    onError: (err) => toast.error(getApiError(err, "Couldn't update that conversation.").message),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => updateConversation(id, { title }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['conversations'] }),
    onError: (err) => toast.error(getApiError(err, "Couldn't rename that conversation.").message),
    onSettled: () => setEditingId(null),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => {
      setConfirmingId(null)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      toast.success('Conversation deleted')
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't delete that conversation.").message),
  })

  const filtered = (query.data?.items ?? []).filter((c) =>
    (c.title ?? 'New conversation').toLowerCase().includes(search.toLowerCase()),
  )
  const { pinned, rest } = splitPinned(filtered)
  const groups = groupByDate(rest)
  const isEmpty = pinned.length === 0 && groups.length === 0

  function renderRow(c: ConversationSummary, index: number) {
    return (
      <ConversationRow
        key={c.id}
        conversation={c}
        index={index}
        editing={editingId === c.id}
        confirming={confirmingId === c.id}
        onStartEdit={() => setEditingId(c.id)}
        onCancelEdit={() => setEditingId(null)}
        onSubmitEdit={(title) => renameMutation.mutate({ id: c.id, title })}
        onTogglePin={() => pinMutation.mutate({ id: c.id, pinned: !c.pinned_at })}
        pinPending={pinMutation.isPending && pinMutation.variables?.id === c.id}
        onRequestDelete={() => setConfirmingId(c.id)}
        onCancelDelete={() => setConfirmingId(null)}
        onConfirmDelete={() => deleteMutation.mutate(c.id)}
        deletePending={deleteMutation.isPending && deleteMutation.variables === c.id}
      />
    )
  }

  return (
    <div>
      <PageHeader
        title="Chat History"
        description="Browse, search, and revisit past conversations"
        actions={
          <Link to="/">
            <Button size="sm">
              <Plus className="h-4 w-4" /> New chat
            </Button>
          </Link>
        }
      />

      <div className="p-6 pb-3">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search conversations"
          className="max-w-sm"
        />
      </div>

      {query.isLoading ? (
        <SkeletonRows rows={8} cols={3} />
      ) : query.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load conversations"
          description={getApiError(query.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : isEmpty ? (
        <StateMessage
          icon={MessagesSquare}
          title={search ? 'No matching conversations' : 'No conversations yet'}
          description={search ? undefined : 'Start a new chat to see it show up here.'}
        />
      ) : (
        <div className="space-y-6 px-6 pb-6">
          {pinned.length > 0 && (
            <div>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-400">Pinned</h2>
              <div className="space-y-1.5">{pinned.map((c, i) => renderRow(c, i))}</div>
            </div>
          )}
          {groups.map((group, gi) => (
            <div key={group.label}>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-400">{group.label}</h2>
              <div className="space-y-1.5">{group.items.map((c, i) => renderRow(c, gi * 10 + i))}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ConversationRow({
  conversation: c,
  index,
  editing,
  confirming,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  onTogglePin,
  pinPending,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  deletePending,
}: {
  conversation: ConversationSummary
  index: number
  editing: boolean
  confirming: boolean
  onStartEdit: () => void
  onCancelEdit: () => void
  onSubmitEdit: (title: string) => void
  onTogglePin: () => void
  pinPending: boolean
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
  deletePending: boolean
}) {
  const [draft, setDraft] = useState(c.title ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!editing) return
    setDraft(c.title ?? '')
    requestAnimationFrame(() => inputRef.current?.select())
  }, [editing, c.title])

  return (
    <div
      className="animate-fade-slide-up flex items-center gap-3 rounded-xl border border-neutral-200 bg-surface px-4 py-3 transition-colors duration-150 hover:border-accent-300"
      style={{ animationDelay: `${Math.min(index, 20) * 25}ms` }}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-100 text-accent-700">
        <MessageSquare className="h-4 w-4" />
      </div>

      {editing ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              onSubmitEdit(draft.trim() || (c.title ?? 'New conversation'))
            }
            if (e.key === 'Escape') onCancelEdit()
          }}
          onBlur={() => onSubmitEdit(draft.trim() || (c.title ?? 'New conversation'))}
          className="min-w-0 flex-1 rounded-lg border border-accent-300 bg-surface px-2.5 py-1.5 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent-400"
        />
      ) : (
        <Link to={`/?conversation=${c.id}`} className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 truncate text-sm font-medium text-ink">
            {c.pinned_at && <Pin className="h-3 w-3 shrink-0 text-accent-500" />}
            {c.title ?? 'New conversation'}
          </p>
          <p className="mt-0.5 text-xs text-neutral-400">
            {c.message_count} {c.message_count === 1 ? 'message' : 'messages'} ·{' '}
            {formatFullDate(c.updated_at ?? c.created_at)}
          </p>
        </Link>
      )}

      {confirming ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="text-xs text-neutral-500">Delete this chat?</span>
          <Button size="sm" variant="danger" loading={deletePending} onClick={onConfirmDelete}>
            <Check className="h-3.5 w-3.5" /> Delete
          </Button>
          <button
            type="button"
            onClick={onCancelDelete}
            className="rounded-md p-1.5 text-neutral-400 transition-colors duration-150 hover:bg-neutral-100 hover:text-ink"
            aria-label="Cancel delete"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        !editing && (
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={onTogglePin}
              disabled={pinPending}
              className={cn(
                'rounded-md p-2 text-neutral-400 transition-colors duration-150 hover:bg-neutral-100 hover:text-ink',
              )}
              aria-label={c.pinned_at ? 'Unpin conversation' : 'Pin conversation'}
            >
              {c.pinned_at ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
            </button>
            <button
              type="button"
              onClick={onStartEdit}
              className="rounded-md p-2 text-neutral-400 transition-colors duration-150 hover:bg-neutral-100 hover:text-ink"
              aria-label="Rename conversation"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onRequestDelete}
              className="rounded-md p-2 text-neutral-400 transition-colors duration-150 hover:bg-red-50 hover:text-red-600"
              aria-label="Delete conversation"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        )
      )}
    </div>
  )
}
