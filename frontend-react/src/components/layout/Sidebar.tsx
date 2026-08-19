import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  History,
  LogOut,
  Monitor,
  Moon,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Settings as SettingsIcon,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import { deleteConversation, listConversations, updateConversation } from '@/api/chat'
import { useAuth } from '@/context/AuthContext'
import { useTheme, type ThemeChoice } from '@/hooks/useTheme'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { groupByDate, splitPinned } from '@/lib/conversationGroups'
import { cn } from '@/lib/cn'
import { isNavItemVisible, SIDEBAR_NAV_ITEMS } from '@/components/layout/nav'
import type { ConversationSummary } from '@/types/chat'

const COLLAPSE_KEY = 'athena-sidebar-collapsed'
const THEME_ICON: Record<ThemeChoice, typeof Sun> = { light: Sun, dark: Moon, system: Monitor }
const NEXT_THEME: Record<ThemeChoice, ThemeChoice> = { light: 'dark', dark: 'system', system: 'light' }

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user, capabilities, hasPermission, logout } = useAuth()
  const { choice: themeChoice, setChoice: setThemeChoice } = useTheme()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const activeConversationId = searchParams.get('conversation')

  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1')
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
      return next
    })
  }

  const query = useQuery({ queryKey: ['conversations'], queryFn: () => listConversations(100) })

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

  const { pinned, rest } = splitPinned(query.data?.items ?? [])
  // Sidebar shows a short, recent-first preview by default — expanding it
  // in place (below) covers the common case without leaving the page;
  // /history is still linked separately since it also offers full-history
  // search plus rename/pin management this inline list doesn't replicate.
  const RECENT_LIMIT = 2
  const truncatedCount = Math.max(0, rest.length - RECENT_LIMIT)
  const groups = groupByDate(historyExpanded ? rest : rest.slice(0, RECENT_LIMIT))
  const workspaceItems = SIDEBAR_NAV_ITEMS.filter((item) => isNavItemVisible(item, hasPermission, user?.role))

  const ThemeIcon = THEME_ICON[themeChoice]
  const initial = (user?.display_name ?? user?.email ?? '?').slice(0, 1).toUpperCase()

  function renderRow(c: ConversationSummary) {
    return (
      <ConversationRow
        key={c.id}
        conversation={c}
        active={activeConversationId === c.id}
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
        onNavigate={onClose}
      />
    )
  }

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-neutral-900/40 md:hidden"
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r border-neutral-200 bg-cream-dark transition-all duration-200 md:static md:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
          collapsed && 'md:w-16',
        )}
      >
        <div className="flex h-14 shrink-0 items-center gap-2 px-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent-600 text-sm font-semibold text-white">
            A
          </div>
          <span className={cn('flex-1 truncate text-sm font-semibold text-ink', collapsed && 'md:hidden')}>
            ATHENA <span className="font-normal text-neutral-400">AI</span>
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="rounded-md p-1 text-neutral-500 hover:bg-neutral-200/60 md:hidden"
          >
            <X className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="hidden shrink-0 rounded-md p-1 text-neutral-400 transition-colors hover:bg-neutral-200/60 hover:text-ink md:flex"
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>

        <div className="shrink-0 px-2.5">
          <Link
            to="/"
            onClick={onClose}
            className={cn(
              'flex items-center gap-2 rounded-lg border border-neutral-300 px-3 py-2 text-sm font-medium text-ink transition-colors hover:bg-neutral-200/50',
              collapsed && 'md:justify-center md:px-0',
            )}
          >
            <Plus className="h-4 w-4 shrink-0" />
            <span className={cn(collapsed && 'md:hidden')}>New chat</span>
          </Link>
        </div>

        <nav className={cn('mt-3 flex-1 space-y-3 overflow-y-auto px-2.5 pb-2', collapsed && 'md:hidden')}>
          {query.isLoading ? (
            <div className="space-y-1.5 px-1 pt-1">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-7 animate-pulse rounded-lg bg-neutral-200/50" />
              ))}
            </div>
          ) : pinned.length === 0 && groups.length === 0 ? (
            <p className="px-1 pt-2 text-xs text-neutral-400">No conversations yet</p>
          ) : (
            <>
              {pinned.length > 0 && (
                <div>
                  <p className="px-1 pb-1 text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
                    Pinned
                  </p>
                  <div className="space-y-0.5">{pinned.map(renderRow)}</div>
                </div>
              )}
              {groups.map((group) => (
                <div key={group.label}>
                  <p className="px-1 pb-1 text-[11px] font-semibold uppercase tracking-wide text-neutral-400">
                    {group.label}
                  </p>
                  <div className="space-y-0.5">{group.items.map(renderRow)}</div>
                </div>
              ))}
            </>
          )}
          {truncatedCount > 0 && (
            <button
              type="button"
              onClick={() => setHistoryExpanded((v) => !v)}
              aria-expanded={historyExpanded}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs font-medium text-neutral-500 transition-colors hover:bg-neutral-200/50 hover:text-ink"
            >
              <span className="flex items-center gap-1.5">
                <History className="h-3.5 w-3.5" />
                {historyExpanded ? 'Show less' : `Show ${truncatedCount} more`}
              </span>
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', historyExpanded && 'rotate-180')} />
            </button>
          )}
          <Link
            to="/history"
            onClick={onClose}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-neutral-500 transition-colors hover:bg-neutral-200/50 hover:text-ink"
          >
            <History className="h-3.5 w-3.5" />
            Open full history
          </Link>
        </nav>

        <div className={cn('shrink-0 space-y-0.5 border-t border-neutral-200 px-2.5 py-2', collapsed && 'md:hidden')}>
          <button
            type="button"
            onClick={() => setWorkspaceOpen((v) => !v)}
            className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-400 transition-colors hover:text-neutral-600"
            aria-expanded={workspaceOpen}
          >
            Workspace
            <ChevronDown className={cn('h-3 w-3 transition-transform', workspaceOpen && 'rotate-180')} />
          </button>
          {workspaceOpen && (
            <div className="animate-fade-slide-up space-y-0.5">
              {workspaceItems.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  onClick={onClose}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors',
                      isActive ? 'bg-accent-100 text-accent-800' : 'text-neutral-600 hover:bg-neutral-200/50 hover:text-ink',
                    )
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {label}
                </NavLink>
              ))}
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-neutral-200 p-2.5">
          <div className={cn('flex items-center gap-1', collapsed && 'md:flex-col')}>
            <button
              type="button"
              onClick={() => setThemeChoice(NEXT_THEME[themeChoice])}
              aria-label={`Theme: ${themeChoice}`}
              title={`Theme: ${themeChoice}`}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-neutral-200/60 hover:text-ink"
            >
              <ThemeIcon className="h-4 w-4" />
            </button>
            <Link
              to="/settings"
              onClick={onClose}
              aria-label="Settings"
              title="Settings"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-neutral-200/60 hover:text-ink"
            >
              <SettingsIcon className="h-4 w-4" />
            </Link>
            <button
              type="button"
              onClick={logout}
              aria-label="Log out"
              title="Log out"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-neutral-200/60 hover:text-ink"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
          <div className={cn('mt-2 flex items-center gap-2 rounded-lg px-1 py-1', collapsed && 'md:justify-center')}>
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-neutral-300 text-xs font-semibold text-neutral-700">
              {initial}
            </div>
            <div className={cn('min-w-0 flex-1', collapsed && 'md:hidden')}>
              <p className="truncate text-sm font-medium text-ink">{user?.display_name ?? user?.email}</p>
              <p className="truncate text-xs text-neutral-500">{capabilities?.display_name ?? user?.role}</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}

function ConversationRow({
  conversation: c,
  active,
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
  onNavigate,
}: {
  conversation: ConversationSummary
  active: boolean
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
  onNavigate: () => void
}) {
  const [draft, setDraft] = useState(c.title ?? '')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!editing) return
    setDraft(c.title ?? '')
    requestAnimationFrame(() => inputRef.current?.select())
  }, [editing, c.title])

  if (confirming) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5">
        <span className="flex-1 truncate text-xs text-neutral-500">Delete this chat?</span>
        <button
          type="button"
          onClick={onConfirmDelete}
          disabled={deletePending}
          aria-label="Confirm delete"
          className="rounded-md p-1 text-red-600 transition-colors hover:bg-red-50"
        >
          <Check className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onCancelDelete}
          aria-label="Cancel delete"
          className="rounded-md p-1 text-neutral-400 transition-colors hover:bg-neutral-200/60"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  if (editing) {
    return (
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
        className="w-full rounded-lg border border-accent-300 bg-surface px-2.5 py-1.5 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent-400"
      />
    )
  }

  return (
    <div
      className={cn(
        'group/row flex items-center gap-0.5 rounded-lg pr-1',
        active ? 'bg-accent-100/70' : 'hover:bg-neutral-200/50',
      )}
    >
      <Link to={`/?conversation=${c.id}`} onClick={onNavigate} className="min-w-0 flex-1 py-1.5 pl-2.5">
        <span
          className={cn(
            'flex items-center gap-1.5 truncate text-sm',
            active ? 'font-medium text-accent-900' : 'text-neutral-700',
          )}
        >
          {c.pinned_at && <Pin className="h-3 w-3 shrink-0 text-accent-500" />}
          <span className="truncate">{c.title ?? 'New conversation'}</span>
        </span>
      </Link>
      <span className="flex shrink-0 items-center opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
        <button
          type="button"
          onClick={onTogglePin}
          disabled={pinPending}
          aria-label={c.pinned_at ? 'Unpin conversation' : 'Pin conversation'}
          className="rounded-md p-1 text-neutral-400 transition-colors hover:bg-neutral-200/60 hover:text-ink"
        >
          {c.pinned_at ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={onStartEdit}
          aria-label="Rename conversation"
          className="rounded-md p-1 text-neutral-400 transition-colors hover:bg-neutral-200/60 hover:text-ink"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onRequestDelete}
          aria-label="Delete conversation"
          className="rounded-md p-1 text-neutral-400 transition-colors hover:bg-red-100 hover:text-red-600"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </span>
    </div>
  )
}
