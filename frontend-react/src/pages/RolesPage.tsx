import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ServerCrash, ShieldCheck } from 'lucide-react'
import { listRoles } from '@/api/roles'
import { getApiError } from '@/lib/apiError'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { SkeletonRows } from '@/components/ui/Skeleton'
import { StateMessage } from '@/components/ui/StateMessage'
import { useCountUp } from '@/hooks/useCountUp'
import { cn } from '@/lib/cn'
import type { RoleSummary } from '@/types/roles'

export function RolesPage() {
  const rolesQuery = useQuery({
    queryKey: ['roles'],
    queryFn: () => listRoles(),
  })
  const total = useCountUp(rolesQuery.data?.roles.length)

  return (
    <div>
      <PageHeader
        title="Roles"
        description="Role and permission configuration"
        actions={
          rolesQuery.data && rolesQuery.data.roles.length > 0 ? (
            <Badge tone="neutral" className="tabular-nums">
              {total.toLocaleString()} {rolesQuery.data.roles.length === 1 ? 'role' : 'roles'}
            </Badge>
          ) : undefined
        }
      />

      {rolesQuery.isLoading ? (
        <SkeletonRows rows={6} cols={3} />
      ) : rolesQuery.isError ? (
        <StateMessage
          icon={ServerCrash}
          tone="error"
          title="Couldn't load roles"
          description={getApiError(rolesQuery.error, 'Something went wrong.').message}
          action={
            <Button size="sm" variant="secondary" onClick={() => void rolesQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        <div className="space-y-3 p-6">
          {rolesQuery.data?.roles.map((role, i) => <RoleCard key={role.role} role={role} index={i} />)}
        </div>
      )}
    </div>
  )
}

function RoleCard({ role, index }: { role: RoleSummary; index: number }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <Card className="animate-fade-slide-up overflow-hidden" style={{ animationDelay: `${index * 40}ms` }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left transition-colors duration-150 hover:bg-neutral-50"
      >
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-transform duration-150',
              expanded && 'scale-105',
              role.all_permissions ? 'bg-accent-100 text-accent-700' : 'bg-neutral-100 text-neutral-500',
            )}
          >
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-ink">{role.display_name}</p>
            <p className="text-xs text-neutral-500">{role.role}</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {role.all_permissions ? (
            <Badge tone="blue">All permissions</Badge>
          ) : (
            <Badge tone="neutral">{role.granted_permissions.length} permissions</Badge>
          )}
          <ChevronDown className={cn('h-4 w-4 text-neutral-400 transition-transform duration-200', expanded && 'rotate-180')} />
        </div>
      </button>

      {expanded && (
        <CardBody className="animate-fade-slide-up border-t border-neutral-200 pt-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Detail label="Default department" value={role.department_default ?? '—'} />
            <Detail label="Model tiers" value={role.tiers_allowed.join(', ') || '—'} />
            <DetailList label="Knowledge departments" values={role.knowledge_departments} />
            <DetailList label="Tools" values={role.tools} />
          </div>
          <div className="mt-4">
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-neutral-400">Permissions</p>
            {role.all_permissions ? (
              <p className="text-sm text-neutral-600">Unrestricted — this role can access every permission-gated feature.</p>
            ) : role.granted_permissions.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {role.granted_permissions.map((p, i) => (
                  <Badge key={p} tone="neutral" className="animate-fade-slide-up" style={{ animationDelay: `${i * 15}ms` }}>
                    {p}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-sm text-neutral-400">No permissions granted</p>
            )}
          </div>
          {Object.keys(role.quotas).length > 0 && (
            <div className="mt-4">
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-neutral-400">Quotas</p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
                {Object.entries(role.quotas).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between gap-2">
                    <dt className="text-neutral-500">{key.replace(/_/g, ' ')}</dt>
                    <dd className="tabular-nums font-medium text-ink">{value === null ? 'Unlimited' : String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </CardBody>
      )}
    </Card>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-0.5 text-sm text-ink">{value}</p>
    </div>
  )
}

function DetailList({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      {values.length > 0 ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {values.map((v) => (
            <Badge key={v} tone="neutral">
              {v}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="mt-0.5 text-sm text-neutral-400">—</p>
      )}
    </div>
  )
}
