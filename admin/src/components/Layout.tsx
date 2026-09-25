import type { ReactNode } from 'react'
import { Activity, LayoutGrid, ShieldCheck, SlidersHorizontal, Users } from 'lucide-react'

export type ScreenId = 'scenarios' | 'decisions' | 'ledger' | 'customers' | 'settings'

const NAV: { id: ScreenId; label: string; icon: typeof LayoutGrid }[] = [
  { id: 'scenarios', label: 'Scenarios', icon: LayoutGrid },
  { id: 'decisions', label: 'Decisions', icon: Activity },
  { id: 'ledger', label: 'Ledger', icon: ShieldCheck },
  { id: 'customers', label: 'Customers', icon: Users },
  { id: 'settings', label: 'Settings', icon: SlidersHorizontal },
]

/**
 * The console shell: a fixed sidebar (desktop-console layout, unlike the
 * customer app's phone frame — this is the operator's side of the wall, not
 * something that ships inside Viseca's "one" app) plus a scrollable content
 * pane. Same Ink and signal palette as `frontend/`, so the two read as one
 * product family in a screen-share.
 */
export function Layout({ screen, onSelect, children }: { screen: ScreenId; onSelect: (s: ScreenId) => void; children: ReactNode }) {
  return (
    <div className="flex h-full min-h-screen bg-ground">
      <aside className="flex w-60 shrink-0 flex-col bg-ink px-4 py-6 text-on-ink">
        <div className="flex items-center gap-2 px-2 pb-8">
          <ShieldCheck size={22} className="text-[#6fa8ff]" />
          <div>
            <p className="text-[13px] font-semibold tracking-[0.02em]">OneGuard</p>
            <p className="text-[11px] text-on-ink-muted">Operator console</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              className={`flex items-center gap-3 rounded-button px-3 py-2.5 text-left text-[13px] font-medium transition-colors ${
                screen === id ? 'bg-white/10 text-on-ink' : 'text-on-ink-soft hover:bg-white/5'
              }`}
            >
              <Icon size={17} strokeWidth={2} />
              {label}
            </button>
          ))}
        </nav>
        <p className="mt-auto px-2 pt-8 text-[11px] leading-relaxed text-on-ink-muted">
          Talks only to this app's own <code>/api/dev/*</code> and read-only endpoints. Never calls Viseca directly, never
          decides a purchase.
        </p>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto px-8 py-7">{children}</main>
    </div>
  )
}
