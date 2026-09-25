import { TEXT_M, TEXT_S } from './style'

export type OpsTab = 'overview' | 'log' | 'health'

const TABS: { id: OpsTab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'log', label: 'Decision log' },
  { id: 'health', label: 'Health' },
]

/** The console's left column: Overview, Decision log, Health (the active one underlined in gold). */
export function OpsTabs({
  active,
  onSelect,
  logCount,
  waiting,
  healthBad,
}: {
  active: OpsTab
  onSelect: (tab: OpsTab) => void
  // Rows in the current run's log, and how many wait for the customer.
  logCount: number | null
  waiting: number
  healthBad: boolean
}) {
  return (
    <div role="tablist" aria-label="Console" className="flex shrink-0 items-end gap-8 border-b border-hairline">
      {TABS.map((tab) => {
        const selected = tab.id === active
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`ops-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`ops-panel-${tab.id}`}
            onClick={() => onSelect(tab.id)}
            className={`${TEXT_M} -mb-px flex min-h-11 items-center gap-2 border-b-[3px] px-1 font-semibold transition-colors ${
              selected ? 'border-[var(--ops-gold)] text-ink' : 'border-transparent text-ink-muted hover:text-ink'
            }`}
          >
            {tab.label}
            {tab.id === 'log' && logCount !== null && (
              <span className={`${TEXT_S} rounded-pill border border-[var(--ops-gold)] px-2 tabular-nums text-ink`}>
                {logCount}
                {waiting > 0 && <span className="text-asked-ink"> · {waiting} waiting</span>}
              </span>
            )}
            {tab.id === 'health' && healthBad && (
              <span className="size-2 rounded-full bg-stopped" aria-label="needs attention" />
            )}
          </button>
        )
      })}
    </div>
  )
}
