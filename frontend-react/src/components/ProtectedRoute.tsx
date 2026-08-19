import type { ReactNode } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { useAuth } from '@/context/AuthContext'
import { FullPageSpinner } from '@/components/ui/Spinner'
import { StateMessage } from '@/components/ui/StateMessage'
import { Button } from '@/components/ui/Button'
import type { Permission, Role } from '@/types/auth'

export function ProtectedRoute({
  children,
  permission,
  denyRoles,
}: {
  children: ReactNode
  permission?: Permission
  // Narrows a permission-gated route further by role — mirrors
  // components/layout/nav.ts's isNavItemVisible() denyRoles, for a route
  // whose backend endpoints enforce a stricter role check than the coarse
  // permission alone (e.g. /evaluation: VIEW_ANALYTICS is necessary but not
  // sufficient — routers/evaluation.py excludes HR specifically).
  denyRoles?: Role[]
}) {
  const { status, hasPermission, user } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullPageSpinner />
  if (status === 'anonymous') {
    // First-time (anonymous) visitors hitting the bare root see the
    // Landing/Architecture explainer before login; every other protected
    // path still goes straight to /login, unchanged.
    const anonymousTarget = location.pathname === '/' ? '/welcome' : '/login'
    return <Navigate to={anonymousTarget} state={{ from: location }} replace />
  }
  if (permission && !hasPermission(permission)) return <AccessRestricted />
  if (user && denyRoles?.includes(user.role)) return <AccessRestricted />

  return <>{children}</>
}

function AccessRestricted() {
  return (
    <StateMessage
      icon={ShieldAlert}
      tone="error"
      title="You don't have access to this page"
      description="Your role doesn't include this permission. Contact an administrator if you think this is a mistake."
      action={
        <Link to="/dashboard">
          <Button variant="secondary" size="sm">
            Back to dashboard
          </Button>
        </Link>
      }
      className="min-h-[60vh]"
    />
  )
}
