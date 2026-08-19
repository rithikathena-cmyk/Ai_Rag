import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { demoLogin as demoLoginRequest, getCapabilities, getCurrentUser, login as loginRequest } from '@/api/auth'
import { setUnauthorizedHandler } from '@/api/client'
import { queryClient } from '@/lib/queryClient'
import { tokenStorage } from '@/lib/tokenStorage'
import { toast } from '@/lib/toast'
import type { Capabilities, CurrentUser, Permission } from '@/types/auth'

interface AuthContextValue {
  user: CurrentUser | null
  capabilities: Capabilities | null
  status: 'loading' | 'authenticated' | 'anonymous'
  login: (email: string, password: string, remember?: boolean) => Promise<void>
  demoLogin: (demoRole: string) => Promise<void>
  logout: () => void
  hasPermission: (permission: Permission) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [status, setStatus] = useState<'loading' | 'authenticated' | 'anonymous'>('loading')

  const logout = useCallback(() => {
    tokenStorage.clear()
    setUser(null)
    setCapabilities(null)
    setStatus('anonymous')
    // Every cached query result (documents, conversations, usage, guardrail
    // analytics, ...) is keyed without a user/role component, so it's only
    // ever correct for whoever fetched it. Without this, logging out and a
    // different account logging back in in the same tab would keep showing
    // the previous account's stale cached values — including counts/data a
    // permission-gated query's own `enabled: hasPermission(...)` flag is
    // there specifically to prevent that new account from fetching itself.
    void queryClient.clear()
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => {
      const wasAuthenticated = Boolean(tokenStorage.getAccess())
      logout()
      if (wasAuthenticated) toast.info('Your session expired — please sign in again.')
    })
  }, [logout])

  useEffect(() => {
    let cancelled = false

    async function hydrate() {
      if (!tokenStorage.getAccess()) {
        // Auto-login as Employee demo user
        try {
          const tokens = await demoLoginRequest('Employee')
          if (cancelled) return
          tokenStorage.set(tokens.access_token, tokens.refresh_token, true)
          const [me, caps] = await Promise.all([getCurrentUser(), getCapabilities()])
          if (cancelled) return
          setUser(me)
          setCapabilities(caps)
          setStatus('authenticated')
        } catch {
          if (cancelled) return
          setStatus('anonymous')
        }
        return
      }
      try {
        const [me, caps] = await Promise.all([getCurrentUser(), getCapabilities()])
        if (cancelled) return
        setUser(me)
        setCapabilities(caps)
        setStatus('authenticated')
      } catch {
        if (cancelled) return
        tokenStorage.clear()
        setStatus('anonymous')
      }
    }

    void hydrate()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string, remember = true) => {
    const tokens = await loginRequest(email, password)
    tokenStorage.set(tokens.access_token, tokens.refresh_token, remember)
    const [me, caps] = await Promise.all([getCurrentUser(), getCapabilities()])
    setUser(me)
    setCapabilities(caps)
    setStatus('authenticated')
  }, [])

  // Demo sessions reuse the exact same token-issuance path as password login
  // (backend/app/routers/auth.py's /auth/demo-login calls the same
  // create_access_token/create_refresh_token as /auth/login), so everything
  // downstream of this point — capabilities, RBAC, guardrails — behaves
  // identically to a normal sign-in for that real seeded account.
  const demoLogin = useCallback(async (demoRole: string) => {
    const tokens = await demoLoginRequest(demoRole)
    tokenStorage.set(tokens.access_token, tokens.refresh_token, true)
    const [me, caps] = await Promise.all([getCurrentUser(), getCapabilities()])
    setUser(me)
    setCapabilities(caps)
    setStatus('authenticated')
  }, [])

  const hasPermission = useCallback(
    (permission: Permission) => {
      if (!capabilities) return false
      return capabilities.all_permissions || capabilities.granted_permissions.includes(permission)
    },
    [capabilities],
  )

  const value = useMemo(
    () => ({ user, capabilities, status, login, demoLogin, logout, hasPermission }),
    [user, capabilities, status, login, demoLogin, logout, hasPermission],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
