import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { FileText, Plus, ServerCrash, UserPlus, Users as UsersIcon, X } from 'lucide-react'
import {
  createUser, getUserDocumentAccess, getUserUsage, listUsers, resetUserUsage, setUserTokenLimit, updateUser,
} from '@/api/users'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { toast } from '@/lib/toast'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { UserDocumentAccess, UserItem } from '@/types/users'

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString()
}

const AVATAR_TONES = [
  'bg-accent-100 text-accent-700',
  'bg-blue-100 text-blue-700',
  'bg-emerald-100 text-emerald-700',
  'bg-amber-100 text-amber-700',
  'bg-violet-100 text-violet-700',
]

function avatarTone(seed: string): string {
  let hash = 0
  for (const ch of seed) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return AVATAR_TONES[hash % AVATAR_TONES.length]
}

function Avatar({ user }: { user: UserItem }) {
  const initial = (user.display_name ?? user.email).charAt(0).toUpperCase()
  return (
    <span
      className={cn(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold',
        avatarTone(user.id),
      )}
    >
      {initial}
    </span>
  )
}

export function UsersPage() {
  const { user: me } = useAuth()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<UserItem | null>(null)
  const [creating, setCreating] = useState(false)

  const usersQuery = useQuery({ queryKey: ['users'], queryFn: () => listUsers() })
  const total = useCountUp(usersQuery.data?.length)

  const canCreate = me?.role === 'admin' || me?.role === 'ceo'

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ['users'] })
  }

  return (
    <div>
      <PageHeader
        title="Users"
        description="Manage user accounts"
        actions={
          <div className="flex items-center gap-3">
            {usersQuery.data && usersQuery.data.length > 0 && (
              <Badge tone="neutral" className="tabular-nums">
                {total.toLocaleString()} {usersQuery.data.length === 1 ? 'user' : 'users'}
              </Badge>
            )}
            {canCreate && (
              <Button size="sm" onClick={() => setCreating(true)}>
                <UserPlus className="h-4 w-4" /> New user
              </Button>
            )}
          </div>
        }
      />

      {usersQuery.isLoading ? (
        <SkeletonRows rows={8} cols={5} />
      ) : usersQuery.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load users"
          description={getApiError(usersQuery.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void usersQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : usersQuery.data && usersQuery.data.length > 0 ? (
        <div className="flex">
          <div className="flex-1 overflow-x-auto p-6">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-neutral-500">
                  <th className="pb-2 font-medium">Email</th>
                  <th className="pb-2 font-medium">Role</th>
                  <th className="pb-2 font-medium">Department</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 font-medium">Joined</th>
                </tr>
              </thead>
              <tbody>
                {usersQuery.data.map((u, i) => (
                  <tr
                    key={u.id}
                    onClick={() => setSelected(u)}
                    className={cn(
                      'animate-fade-slide-up cursor-pointer border-b border-neutral-100 transition-colors duration-150 hover:bg-neutral-50',
                      selected?.id === u.id && 'bg-accent-50/60',
                    )}
                    style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}
                  >
                    <td className="py-2.5 pr-4 font-medium text-ink">
                      <span className="inline-flex items-center gap-2.5">
                        <Avatar user={u} />
                        <span>
                          {u.display_name ?? u.email}
                          {u.display_name && <span className="ml-1.5 text-xs font-normal text-neutral-400">{u.email}</span>}
                        </span>
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 text-neutral-600">{u.role}</td>
                    <td className="py-2.5 pr-4 text-neutral-600">{u.department ?? '—'}</td>
                    <td className="py-2.5 pr-4">
                      <Badge tone={u.is_active ? 'green' : 'neutral'}>{u.is_active ? 'Active' : 'Inactive'}</Badge>
                    </td>
                    <td className="py-2.5 pr-4 text-neutral-600">{formatDate(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <UserDetailPanel
              // Forces a full remount on every selection change — without
              // this, React reuses the same component instance for a
              // different `user` prop, and the form's local useState
              // (role/department/active/limit fields) keeps whichever
              // user's values it initialized with. Found live: selecting
              // Employee 1 then HR 1 left the Role/Department fields
              // showing Employee 1's "user"/"manufacturing" while every
              // query-driven section (Document access, Usage) correctly
              // showed HR 1's real data — an admin hitting "Save changes"
              // at that point would silently overwrite HR 1 with stale
              // values from a different account.
              key={selected.id}
              user={selected}
              onClose={() => setSelected(null)}
              onChanged={invalidate}
            />
          )}
        </div>
      ) : (
        <StateMessage icon={UsersIcon} title="No users found" />
      )}

      {creating && <CreateUserPanel onClose={() => setCreating(false)} onCreated={invalidate} />}
    </div>
  )
}

function UsageBar({
  label,
  used,
  limit,
  format = (n: number) => n.toLocaleString(),
}: {
  label: string
  used: number
  limit: number | null
  format?: (n: number) => string
}) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : null
  const tone = pct != null && pct > 90 ? 'bg-red-500' : pct != null && pct > 70 ? 'bg-amber-500' : 'bg-accent-500'
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-neutral-500">{label}</span>
        <span className="font-medium tabular-nums text-ink">
          {format(used)}
          {limit != null ? ` / ${format(limit)}` : ' · Unlimited'}
        </span>
      </div>
      {pct != null && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
          <div className={cn('h-full rounded-full transition-all duration-700 ease-out', tone)} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}

function UserDetailPanel({
  user,
  onClose,
  onChanged,
}: {
  user: UserItem
  onClose: () => void
  onChanged: () => void
}) {
  const { user: me, hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const [role, setRole] = useState(user.role)
  const [department, setDepartment] = useState(user.department ?? '')
  const [isActive, setIsActive] = useState(user.is_active)
  const [dailyLimit, setDailyLimit] = useState(user.daily_token_limit_override?.toString() ?? '')
  const [monthlyLimit, setMonthlyLimit] = useState(user.monthly_token_limit_override?.toString() ?? '')

  const canEditAccount = hasPermission('MANAGE_USERS')
  const canManageLimits = me?.role === 'admin' || me?.role === 'ceo'

  const usageQuery = useQuery({
    queryKey: ['users', user.id, 'usage'],
    queryFn: () => getUserUsage(user.id),
    enabled: canManageLimits,
  })

  // Gated on VIEW_USERS server-side — the same permission that already lets
  // this panel be open at all, so no extra client-side check here.
  const accessQuery = useQuery({
    queryKey: ['users', user.id, 'document-access'],
    queryFn: () => getUserDocumentAccess(user.id),
  })

  const updateMutation = useMutation({
    mutationFn: () => updateUser(user.id, { role, department: department || undefined, is_active: isActive }),
    onSuccess: () => {
      toast.success('User updated')
      onChanged()
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't update that user.").message),
  })

  const limitMutation = useMutation({
    mutationFn: () =>
      setUserTokenLimit(user.id, {
        daily_tokens: dailyLimit ? Number(dailyLimit) : null,
        monthly_tokens: monthlyLimit ? Number(monthlyLimit) : null,
      }),
    onSuccess: () => {
      toast.success('Token limit updated')
      onChanged()
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't update the token limit.").message),
  })

  const resetMutation = useMutation({
    mutationFn: () => resetUserUsage(user.id),
    onSuccess: () => {
      toast.success('Usage reset')
      void queryClient.invalidateQueries({ queryKey: ['users', user.id, 'usage'] })
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't reset usage.").message),
  })

  return (
    <div className="w-96 shrink-0 animate-slide-in-right border-l border-neutral-200 p-5">
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2.5">
          <Avatar user={user} />
          <div>
            <h3 className="text-sm font-semibold text-ink">{user.display_name ?? user.email}</h3>
            <p className="text-xs text-neutral-500">{user.email}</p>
          </div>
        </div>
        <button type="button" onClick={onClose} className="text-xs text-neutral-400 transition-colors hover:text-ink">
          Close
        </button>
      </div>

      {canEditAccount ? (
        <div className="space-y-3">
          <Field label="Role">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserItem['role'])}
              className="w-full rounded-lg border border-neutral-300 bg-surface px-3 py-2 text-sm text-ink transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-accent-400"
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Department">
            <Input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="e.g. engineering" />
          </Field>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
            Active
          </label>
          <Button size="sm" loading={updateMutation.isPending} onClick={() => updateMutation.mutate()}>
            Save changes
          </Button>
        </div>
      ) : (
        <dl className="space-y-2 text-sm">
          <Detail label="Role" value={user.role} />
          <Detail label="Department" value={user.department ?? '—'} />
          <Detail label="Status" value={user.is_active ? 'Active' : 'Inactive'} />
        </dl>
      )}

      <DocumentAccessSection query={accessQuery} />

      {canManageLimits && (
        <div className="mt-5 animate-fade-slide-up border-t border-neutral-200 pt-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">Usage</p>
          {usageQuery.isLoading ? (
            <div className="space-y-3">
              {[0, 1].map((i) => (
                <div key={i} className="h-8 animate-pulse rounded bg-neutral-100" />
              ))}
            </div>
          ) : usageQuery.data ? (
            <div className="space-y-3">
              <UsageBar label="Daily tokens" used={usageQuery.data.daily_tokens_used} limit={usageQuery.data.daily_tokens_limit} />
              <UsageBar label="Monthly tokens" used={usageQuery.data.monthly_tokens_used} limit={usageQuery.data.monthly_tokens_limit} />
              <UsageBar
                label="Monthly cost"
                used={usageQuery.data.monthly_cost_usd_used}
                limit={usageQuery.data.monthly_cost_usd_limit}
                format={(n) => `$${n.toFixed(2)}`}
              />
            </div>
          ) : null}

          <div className="mt-3 space-y-2">
            <Field label="Daily token limit override">
              <Input
                type="number"
                min={1}
                value={dailyLimit}
                onChange={(e) => setDailyLimit(e.target.value)}
                placeholder="Role default"
              />
            </Field>
            <Field label="Monthly token limit override">
              <Input
                type="number"
                min={1}
                value={monthlyLimit}
                onChange={(e) => setMonthlyLimit(e.target.value)}
                placeholder="Role default"
              />
            </Field>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" loading={limitMutation.isPending} onClick={() => limitMutation.mutate()}>
                Save limits
              </Button>
              <Button size="sm" variant="ghost" loading={resetMutation.isPending} onClick={() => resetMutation.mutate()}>
                Reset usage
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const CLASSIFICATION_TONE: Record<string, 'red' | 'neutral'> = { restricted: 'red' }

/** What this account can actually see and do with documents — the exact
 * same two-stage RBAC filter (department/category, then the per-document
 * permission ACL) the real /documents page and chat-time retrieval apply,
 * run for this user instead of the viewer. `can_view` is the document
 * LIBRARY page specifically; the list below is retrieval visibility, a
 * separate axis — a role with can_view=false (e.g. Employee) can still have
 * documents here, because chat can retrieve from them even without library
 * browsing access. */
function DocumentAccessSection({ query }: { query: UseQueryResult<UserDocumentAccess, unknown> }) {
  return (
    <div className="mt-5 animate-fade-slide-up border-t border-neutral-200 pt-4">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-neutral-400">
        <FileText className="h-3.5 w-3.5" /> Document access
      </p>

      {query.isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-6 animate-pulse rounded bg-neutral-100" />
          ))}
        </div>
      ) : query.isError ? (
        <p className="text-xs text-neutral-400">Couldn't load document access.</p>
      ) : query.data ? (
        <>
          <div className="flex flex-wrap gap-1.5">
            <Badge tone={query.data.can_view ? 'green' : 'neutral'}>{query.data.can_view ? 'Can browse library' : 'No library access'}</Badge>
            {query.data.can_upload && <Badge tone="blue">Upload</Badge>}
            {query.data.can_delete && <Badge tone="amber">Delete</Badge>}
            {query.data.can_manage && <Badge tone="blue">Manage</Badge>}
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            {query.data.knowledge_departments === null
              ? 'No department restriction'
              : query.data.knowledge_departments.length === 0
                ? 'No department scope assigned'
                : `Scoped to: ${query.data.knowledge_departments.join(', ')}`}
          </p>

          <p className="mb-1.5 mt-3 text-xs text-neutral-500">
            {query.data.total_visible} document{query.data.total_visible === 1 ? '' : 's'} retrievable via chat
          </p>
          {query.data.documents.length === 0 ? (
            <p className="text-xs text-neutral-400">None</p>
          ) : (
            <ul className="max-h-48 space-y-1 overflow-y-auto">
              {query.data.documents.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-2 rounded-md px-1.5 py-1 text-xs hover:bg-neutral-50">
                  <span className="truncate text-ink">{d.title}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {d.department && <span className="text-neutral-400">{d.department}</span>}
                    {d.security_classification !== 'internal' && (
                      <Badge tone={CLASSIFICATION_TONE[d.security_classification] ?? 'neutral'}>
                        {d.security_classification}
                      </Badge>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  )
}

const ROLE_OPTIONS = [
  'admin', 'user', 'hr', 'project_manager', 'ceo', 'plant_manager', 'production_manager',
  'production_supervisor', 'operator', 'maintenance_engineer', 'maintenance_manager',
  'quality_engineer', 'warehouse_staff', 'inventory_manager', 'procurement_officer', 'planner',
]

function CreateUserPanel({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('user')
  const [department, setDepartment] = useState('')

  const createMutation = useMutation({
    mutationFn: () =>
      createUser({
        email,
        password,
        display_name: displayName || undefined,
        role,
        department: department || undefined,
      }),
    onSuccess: () => {
      toast.success('User created')
      onCreated()
      onClose()
    },
    onError: (err) => toast.error(getApiError(err, "Couldn't create that user.").message),
  })

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div
        className="w-full max-w-sm animate-fade-slide-up rounded-xl border border-neutral-200 bg-surface p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-ink">New user</h3>
          <button type="button" onClick={onClose} className="text-neutral-400 hover:text-ink">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3">
          <Field label="Email">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="jane@company.com" />
          </Field>
          <Field label="Display name">
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Jane Doe" />
          </Field>
          <Field label="Password">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Min 8 characters" />
          </Field>
          <Field label="Role">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-lg border border-neutral-300 bg-surface px-3 py-2 text-sm text-ink transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-accent-400"
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Department">
            <Input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="e.g. engineering" />
          </Field>
          <Button
            className="w-full"
            loading={createMutation.isPending}
            disabled={!email || password.length < 8}
            onClick={() => createMutation.mutate()}
          >
            <Plus className="h-4 w-4" /> Create user
          </Button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-neutral-500">{label}</label>
      {children}
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
