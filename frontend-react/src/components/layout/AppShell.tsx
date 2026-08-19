import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Menu, WifiOff } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { useOnlineStatus } from '@/hooks/useOnlineStatus'

export function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const online = useOnlineStatus()

  return (
    <div className="flex h-screen w-full overflow-hidden bg-cream">
      <Sidebar open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        {!online && (
          <div className="flex shrink-0 items-center justify-center gap-2 bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-800">
            <WifiOff className="h-3.5 w-3.5" /> You're offline — some features may not work until you reconnect.
          </div>
        )}
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-neutral-200 px-3 md:hidden">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="rounded-md p-1.5 text-neutral-600 hover:bg-neutral-100"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-sm font-semibold text-ink">AI Guardrails</span>
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
