import type { LucideIcon } from 'lucide-react'
import {
  ClipboardCheck,
  FileText,
  LayoutDashboard,
  LineChart,
  Route,
  ScrollText,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from 'lucide-react'
import type { Permission, Role } from '@/types/auth'

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  permission?: Permission
  // Narrows a permission-gated item further by role — for the rare case
  // where a role holds the permission for another reason (HR has
  // VIEW_ANALYTICS for Metrics) but a *different*, stricter, server-side
  // role check on the underlying endpoints excludes it anyway (Evaluation
  // below).
  denyRoles?: Role[]
}

export function isNavItemVisible(item: NavItem, hasPermission: (permission: Permission) => boolean, role?: Role): boolean {
  if (item.permission && !hasPermission(item.permission)) return false
  if (role && item.denyRoles?.includes(role)) return false
  return true
}

/** Workspace/enterprise tools — demoted to a secondary, collapsible section in
 * the Sidebar (and reused as Dashboard's "Quick access" tiles). Chat itself
 * lives at "/" and isn't part of this list: it's the app's primary surface,
 * reached through the sidebar's own "New chat" button and recent-conversation
 * list rather than a flat nav entry. */
export const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/documents', label: 'Documents', icon: FileText, permission: 'VIEW_DOCUMENTS' },
  // routers/evaluation.py restricts every endpoint to ADMIN/CEO/PROJECT_MANAGER
  // — narrower than the VIEW_ANALYTICS permission gate below, which EVERY role
  // now holds for the Metrics dashboards. So the roles that hold the
  // permission but not the role grant (HR, Employee) must be denied here
  // specifically, or the nav link would 403 the moment they used it.
  { to: '/evaluation', label: 'Evaluation', icon: ClipboardCheck, permission: 'VIEW_ANALYTICS', denyRoles: ['hr', 'user'] },
  { to: '/metrics', label: 'Metrics', icon: LineChart, permission: 'VIEW_ANALYTICS' },
  { to: '/users', label: 'Users', icon: Users, permission: 'VIEW_USERS' },
  { to: '/roles', label: 'Roles', icon: ShieldCheck, permission: 'VIEW_ROLES' },
  { to: '/audit-logs', label: 'Audit Logs', icon: ScrollText, permission: 'VIEW_AUDIT_LOGS' },
  // No permission gate — every role can open this now, scoped server-side
  // to their own request history (routers/traces.py); only VIEW_AUDIT_LOGS
  // roles (CEO/Admin) see across users, which the page itself adapts to.
  { to: '/traces', label: 'Traces', icon: Route },
  { to: '/guardrail-policies', label: 'Guardrail Policies', icon: ShieldAlert, permission: 'MANAGE_GUARDRAIL_POLICIES' },
  { to: '/admin', label: 'Admin', icon: Settings, permission: 'SYSTEM_SETTINGS' },
]

// Sidebar's "Workspace" section shows a trimmed subset of NAV_ITEMS — just
// the four surfaces requested there (Guardrail Policies, Traces, Users,
// Metrics). A separate allowlist rather than editing NAV_ITEMS itself,
// because NAV_ITEMS is also reused for Dashboard's "Quick access" tiles
// (see that array's own comment) — trimming it directly would have quietly
// shrunk Dashboard's tiles too, which nobody asked for.
const SIDEBAR_VISIBLE_PATHS = new Set(['/guardrail-policies', '/traces', '/users', '/metrics'])
export const SIDEBAR_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter((item) => SIDEBAR_VISIBLE_PATHS.has(item.to))

export const SECONDARY_NAV_ITEMS: NavItem[] = [{ to: '/settings', label: 'Settings', icon: SlidersHorizontal }]
