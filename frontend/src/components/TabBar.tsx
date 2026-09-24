import type { ComponentType } from 'react'
import { AccountsIcon, BellIcon, HomeIcon, ShieldIcon } from './icons/lucide'
import type { IconProps } from './icons/IconProps'

export type TabId = 'home' | 'accounts' | 'activity' | 'approvals'

const TABS: { id: TabId; label: string; Icon: ComponentType<IconProps> }[] = [
  { id: 'home', label: 'Home', Icon: HomeIcon },
  { id: 'accounts', label: 'Accounts', Icon: AccountsIcon },
  // Shield/leash doubles as the Activity tab icon (DESIGN.md §Icons).
  { id: 'activity', label: 'Activity', Icon: ShieldIcon },
  { id: 'approvals', label: 'Approvals', Icon: BellIcon },
]

/** DESIGN.md §Layout conventions: fixed bottom tab bar, every screen but sign-in and new-policy. */
export function TabBar({
  active,
  onSelect,
  approvalsCount = 0,
}: {
  active: TabId
  onSelect: (tab: TabId) => void
  approvalsCount?: number
}) {
  return (
    <nav
      aria-label="Main"
      className="flex flex-none border-t border-hairline bg-surface px-3 pt-3 pb-[22px]"
    >
      {TABS.map(({ id, label, Icon }) => {
        const isActive = id === active
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            aria-current={isActive ? 'page' : undefined}
            className={`flex flex-1 flex-col items-center gap-1 py-1 ${
              isActive ? 'text-ink' : 'text-ink-tab'
            }`}
          >
            {/*
              No pill behind the active icon: the design system's recalibration
              against the Viseca "one" app found a plain tab bar there, so the
              active tab is carried by icon and label colour alone (`text-ink`
              vs `text-ink-tab` on the button). The fixed box stays — it holds
              the row height steady and anchors the approvals badge.
            */}
            <span className="flex h-[30px] w-14 items-center justify-center">
              <span className="relative inline-flex">
                <Icon size={22} strokeWidth={1.8} filled={isActive} />
                {id === 'approvals' && approvalsCount > 0 && (
                  <span
                    aria-hidden="true"
                    className="absolute -top-1.5 -right-1.5 flex size-[18px] items-center justify-center rounded-full bg-alert-badge text-[10px] font-semibold text-on-ink"
                  >
                    {approvalsCount > 9 ? '9+' : approvalsCount}
                  </span>
                )}
              </span>
            </span>
            {/*
              Shape and weight, not colour, carry the active state now that the
              pill is gone: the icon is drawn solid (`filled` above) and the
              label goes semibold. Colour alone would be the one thing this
              project's accessibility convention rules out. `aria-current`
              covers assistive tech either way.
            */}
            <span className={`text-[11px] ${isActive ? 'font-semibold' : 'font-medium'}`}>
              {label}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
