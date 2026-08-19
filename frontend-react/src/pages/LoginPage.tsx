import { type FormEvent, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Eye, EyeOff, Lock, Mail, Route, Shield, ShieldCheck, User } from 'lucide-react'
import { listDemoUsers } from '@/api/auth'
import { useAuth } from '@/context/AuthContext'
import { getApiError } from '@/lib/apiError'
import { cn } from '@/lib/cn'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Skeleton } from '@/components/ui/Skeleton'
import type { DemoUserTile } from '@/types/auth'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// Decorative only — never sent anywhere. A demo tile authenticates through
// the secure role-based POST /auth/demo-login (see AuthContext.demoLogin),
// never through this form's real password submission. This string just
// fills the masked password field so the fill-and-submit animation looks
// like a real sign-in without a real credential ever reaching the browser.
const DEMO_PASSWORD_DISPLAY = 'demo-access-2026'

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms))
}

async function typeInto(setter: (value: string) => void, text: string, charDelayMs = 18) {
  for (let i = 1; i <= text.length; i++) {
    setter(text.slice(0, i))
    await sleep(charDelayMs)
  }
}

export function LoginPage() {
  const { status, hasPermission } = useAuth()
  const location = useLocation()

  if (status === 'authenticated') {
    const fallback = hasPermission('CHAT') ? '/' : '/dashboard'
    const redirectTo = (location.state as { from?: Location })?.from?.pathname ?? fallback
    return <Navigate to={redirectTo} replace />
  }

  return (
    <div className="grid h-screen w-full overflow-y-auto bg-cream lg:grid-cols-2 lg:overflow-hidden">
      <div className="flex min-h-full items-center justify-center px-6 py-6 sm:px-10">
        <div className="w-full max-w-sm animate-fade-slide-up">
          <div className="mb-5 flex flex-col items-center gap-2 text-center">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-600 text-base font-semibold text-white transition-transform duration-300 hover:scale-105">
              G
            </div>
            <div>
              <h1 className="text-lg font-semibold text-ink">Sign in to AI Guardrails</h1>
              <p className="mt-0.5 text-sm text-neutral-500">Enterprise AI security & control</p>
            </div>
          </div>

          <LoginPanel />
        </div>
      </div>
      <HeroPanel />
    </div>
  )
}

function LoginPanel() {
  const { login, demoLogin } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [touched, setTouched] = useState<{ email?: boolean; password?: boolean }>({})
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [shake, setShake] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // The demo_role currently being animated/submitted via a tile click, or
  // null. Distinct from `submitting`: filling covers the typing animation
  // too, before the real network call starts.
  const [demoFilling, setDemoFilling] = useState<string | null>(null)

  const locked = demoFilling !== null || submitting
  const emailError = touched.email && !EMAIL_PATTERN.test(email) ? 'Enter a valid email address' : undefined
  const passwordError = touched.password && !password ? 'Password is required' : undefined
  const canSubmit = EMAIL_PATTERN.test(email) && password.length > 0

  function triggerShake() {
    setShake(true)
    window.setTimeout(() => setShake(false), 400)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (locked) return
    setTouched({ email: true, password: true })

    if (!canSubmit) {
      triggerShake()
      return
    }

    setError(null)
    setSubmitting(true)
    try {
      await login(email, password, remember)
    } catch (err) {
      setError(getApiError(err, 'Invalid email or password').message)
      triggerShake()
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDemoSelect(tile: DemoUserTile) {
    if (locked) return
    setError(null)
    setNotice(null)
    setTouched({})
    setDemoFilling(tile.demo_role)
    setEmail('')
    setPassword('')

    // A demo tile means "show me a fresh view as this role" — never "resume
    // whatever page I was bounced here from." Without this, a stale
    // location.state.from (left over from an earlier, unrelated redirect to
    // /login — e.g. a session that expired while on an admin-only page)
    // would send every subsequent demo login back to that same page,
    // regardless of whether the newly-chosen role can even open it. Clearing
    // it here means the post-auth redirect below always falls through to the
    // role-appropriate home instead.
    navigate(location.pathname, { replace: true, state: null })

    await typeInto(setEmail, tile.email)
    await sleep(150)
    await typeInto(setPassword, DEMO_PASSWORD_DISPLAY)
    await sleep(250)

    setSubmitting(true)
    try {
      // The visible form appears to submit, but the real credential never
      // leaves the server — this calls the secure role-based demo-login
      // endpoint, not a password login with the placeholder text above.
      await demoLogin(tile.demo_role)
    } catch (err) {
      setError(getApiError(err, 'Could not sign in with this demo account').message)
      triggerShake()
      setEmail('')
      setPassword('')
    } finally {
      setSubmitting(false)
      setDemoFilling(null)
    }
  }

  return (
    <>
      <form
        onSubmit={handleSubmit}
        noValidate
        className={cn(
          'space-y-3 rounded-xl border border-neutral-200 bg-surface p-5 shadow-sm transition-shadow duration-200 focus-within:shadow-md',
          shake && 'animate-shake',
        )}
      >
        <div>
          <label htmlFor="email" className="mb-1.5 block text-sm font-medium text-neutral-700">
            Email
          </label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            autoFocus
            required
            readOnly={locked}
            value={email}
            error={emailError}
            startAdornment={<Mail className="h-4 w-4" />}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, email: true }))}
          />
        </div>

        <div>
          <label htmlFor="password" className="mb-1.5 block text-sm font-medium text-neutral-700">
            Password
          </label>
          <Input
            id="password"
            type={showPassword ? 'text' : 'password'}
            autoComplete="current-password"
            required
            readOnly={locked}
            value={password}
            error={passwordError}
            startAdornment={<Lock className="h-4 w-4" />}
            onChange={(e) => setPassword(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, password: true }))}
            endAdornment={
              <button
                type="button"
                tabIndex={-1}
                disabled={locked}
                onClick={() => setShowPassword((v) => !v)}
                className="text-neutral-400 transition-colors hover:text-neutral-600 disabled:opacity-60"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
        </div>

        <div className="flex items-center justify-between text-sm">
          <label className="flex items-center gap-2 text-neutral-600">
            <input
              type="checkbox"
              checked={remember}
              disabled={locked}
              onChange={(e) => setRemember(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-neutral-300 text-accent-600 focus:ring-accent-400"
            />
            Remember me
          </label>
          <button
            type="button"
            disabled={locked}
            onClick={() => setNotice('Contact your administrator to reset your password.')}
            className="font-medium text-accent-700 transition-colors hover:text-accent-800 disabled:opacity-60"
          >
            Forgot password?
          </button>
        </div>

        {notice && <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs text-accent-800">{notice}</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}

        <Button
          type="submit"
          size="lg"
          disabled={locked}
          className="w-full bg-gradient-to-r from-accent-500 to-accent-700 hover:from-accent-600 hover:to-accent-800"
          loading={submitting}
        >
          Sign in
        </Button>
      </form>

      <DemoUsersSection demoFilling={demoFilling} locked={locked} onSelect={(tile) => void handleDemoSelect(tile)} />
    </>
  )
}

function DemoUsersSection({
  demoFilling,
  locked,
  onSelect,
}: {
  demoFilling: string | null
  locked: boolean
  onSelect: (tile: DemoUserTile) => void
}) {
  const query = useQuery({ queryKey: ['auth', 'demo-users'], queryFn: listDemoUsers })

  if (query.isLoading) {
    return (
      <div className="mt-4">
        <Divider label="or explore as a demo user" />
        <div className="mt-3 grid grid-cols-3 gap-2">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-[4.5rem] rounded-lg" />
          ))}
        </div>
      </div>
    )
  }

  // Silent, not an error state for the page — password login still works
  // fully; the demo tiles are a bonus surface, not a dependency.
  if (query.isError || !query.data?.enabled || query.data.users.length === 0) {
    return null
  }

  return (
    <div className="mt-4">
      <Divider label="or explore as a demo user" />
      <div className="mt-3 grid grid-cols-3 gap-2">
        {query.data.users.map((tile) => (
          <DemoUserCard
            key={tile.demo_role}
            tile={tile}
            active={demoFilling === tile.demo_role}
            disabled={locked && demoFilling !== tile.demo_role}
            onSelect={() => onSelect(tile)}
          />
        ))}
      </div>
    </div>
  )
}

function DemoUserCard({
  tile,
  active,
  disabled,
  onSelect,
}: {
  tile: DemoUserTile
  active: boolean
  disabled: boolean
  onSelect: () => void
}) {
  const Icon = tile.is_privileged ? Shield : User
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      className={cn(
        'group flex flex-col items-start gap-1.5 rounded-lg border border-neutral-200 bg-surface p-2.5 text-left transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-accent-300 hover:bg-accent-50/60 hover:shadow-md',
        'active:translate-y-0 active:scale-[0.98] active:shadow-sm active:duration-75',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-400 focus-visible:ring-offset-2',
        'disabled:pointer-events-none disabled:opacity-60',
        active && 'border-accent-300 bg-accent-50/60 shadow-sm',
      )}
    >
      <span className="flex w-full items-center justify-between">
        <span
          className={cn(
            'flex h-7 w-7 items-center justify-center rounded-full bg-accent-100 text-accent-600 transition-colors duration-200',
            'group-hover:bg-accent-600 group-hover:text-white',
            active && 'bg-accent-600 text-white',
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
        {active ? (
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-accent-500 border-t-transparent" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 -translate-x-1 text-neutral-300 opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:text-accent-600 group-hover:opacity-100" />
        )}
      </span>
      <span className="text-xs font-medium leading-tight text-ink sm:text-sm">{tile.display_name}</span>
      <span className="text-[11px] leading-tight text-neutral-500">{tile.description}</span>
    </button>
  )
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-px flex-1 bg-neutral-200" />
      <span className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</span>
      <span className="h-px flex-1 bg-neutral-200" />
    </div>
  )
}

const HERO_POINTS = [
  { icon: ShieldCheck, text: 'Role-based access to enterprise knowledge' },
  { icon: Shield, text: 'Guardrails on every request — PII, injection, scope' },
  { icon: Route, text: 'Full audit trail, down to every check' },
]

function HeroPanel() {
  return (
    <div className="relative hidden overflow-hidden bg-gradient-to-br from-accent-600 via-accent-700 to-accent-900 lg:flex lg:flex-col lg:justify-center lg:px-16">
      <div
        className="animate-float-slow absolute -left-24 -top-24 h-96 w-96 rounded-full bg-accent-300 opacity-25 blur-3xl"
        aria-hidden="true"
      />
      <div
        className="animate-float-slow absolute -bottom-32 -right-16 h-[28rem] w-[28rem] rounded-full bg-accent-900 opacity-40 blur-3xl"
        style={{ animationDelay: '-5s' }}
        aria-hidden="true"
      />
      <div
        className="animate-float-slow absolute right-1/3 top-1/4 h-64 w-64 rounded-full bg-accent-400 opacity-20 blur-3xl"
        style={{ animationDelay: '-9s' }}
        aria-hidden="true"
      />

      <div className="relative max-w-md">
        <p className="text-sm font-medium uppercase tracking-wide text-accent-100">Enterprise AI</p>
        <h2 className="mt-3 text-3xl font-semibold text-white" style={{ textWrap: 'balance' }}>
          Secure intelligence, governed by design.
        </h2>
        <p className="mt-4 text-accent-50/90">
          Every answer is grounded in your organization's documents and shaped by exactly what your role is
          permitted to see.
        </p>
        <ul className="mt-8 space-y-4">
          {HERO_POINTS.map(({ icon: Icon, text }) => (
            <li key={text} className="flex items-center gap-3 text-sm text-white">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10">
                <Icon className="h-4 w-4" />
              </span>
              {text}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
