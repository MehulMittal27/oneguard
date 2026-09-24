import { useState } from 'react'
import type { Decision } from '../../api/types'
import { DecisionMark } from '../../components/DecisionMark'
import { dateKey, formatShortDate } from '../../lib/datetime'
import { useDecisions } from '../../state/DecisionsContext'
import { DecisionDetail } from '../DecisionDetail/DecisionDetail'

// Only 3 real categories (D-041) — "expired" and a human step-up answer are
// statuses of Uncertain, not their own filter. Exported so Home's
// `OverviewHero` counts and App's cross-tab navigation share this one type.
export type FilterId = 'all' | 'approved' | 'stopped' | 'uncertain'

const FILTERS: { id: FilterId; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'approved', label: 'Approved' },
  { id: 'stopped', label: 'Stopped' },
  { id: 'uncertain', label: 'Uncertain' },
]

// "All" is neutral (ink); the other three carry their decision color both at
// rest and active — never color-only, since each tab still shows its own
// count + label text regardless of state.
const FILTER_STYLE: Record<FilterId, string> = {
  all: 'text-ink',
  approved: 'text-approved',
  stopped: 'text-stopped',
  uncertain: 'text-asked',
}

export function Activity({
  onViewPolicy,
  onGoToApprovals,
  onGoHome,
  initialFilter,
}: {
  onViewPolicy: (cardId: string) => void
  onGoToApprovals: () => void
  onGoHome: () => void
  // Set when Home's OverviewHero or "See all" opened this tab pre-filtered.
  // Read once into initial state, not a controlled prop — Activity fully
  // unmounts/remounts on every tab switch (App.tsx has no `key` trick to
  // work around), so a fresh mount always sees the current value.
  initialFilter?: FilterId
}) {
  const { decisions, status, retry } = useDecisions()
  const [filter, setFilter] = useState<FilterId>(initialFilter ?? 'all')
  const [viewingId, setViewingId] = useState<string | null>(null)

  const counts = {
    all: decisions.length,
    approved: decisions.filter((d) => d.decision === 'approved').length,
    stopped: decisions.filter((d) => d.decision === 'stopped').length,
    uncertain: decisions.filter((d) => d.decision === 'uncertain').length,
  }

  const filtered = decisions
    .filter((d) => filter === 'all' || d.decision === filter)
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))

  const groups: { key: string; label: string; rows: Decision[] }[] = []
  for (const decision of filtered) {
    const key = dateKey(decision.occurred_at)
    const group = groups.at(-1)
    if (group && group.key === key) {
      group.rows.push(decision)
    } else {
      groups.push({ key, label: formatShortDate(decision.occurred_at), rows: [decision] })
    }
  }

  // A still-pending uncertain purchase is actionable, not just viewable —
  // route straight to where it can actually be answered.
  function selectDecision(decision: Decision) {
    if (decision.status === 'pending_human') {
      onGoToApprovals()
    } else {
      setViewingId(decision.authorization_id)
    }
  }

  if (viewingId) {
    return (
      <DecisionDetail
        authorizationId={viewingId}
        backLabel="Activity"
        onBack={() => setViewingId(null)}
        onSelectRelated={setViewingId}
        onViewPolicy={onViewPolicy}
        onGoToApprovals={onGoToApprovals}
        onGoHome={onGoHome}
      />
    )
  }

  return (
    <div className="flex flex-col gap-7 px-8 pt-9 pb-9 sm:pt-5">
      <div>
        <h1 className="font-display text-[30px] font-bold text-ink">Activity</h1>
        <p className="text-[15px] text-ink-muted">Every purchase your agent has proposed.</p>
      </div>

      <div className="flex gap-6 border-b border-hairline" role="tablist" aria-label="Filter by decision">
        {FILTERS.map(({ id, label }) => {
          const isActive = id === filter
          const color = FILTER_STYLE[id]
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setFilter(id)}
              className={`flex min-h-11 items-center gap-1.5 border-b-[2.5px] pb-2.5 text-[13px] ${color} ${
                isActive ? 'border-current font-bold' : 'border-transparent font-medium'
              }`}
            >
              {label}
              <span className="tabular-nums">{counts[id]}</span>
            </button>
          )
        })}
      </div>

      {status === 'loading' && (
        <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-row bg-surface-sunken" />
          ))}
          <span className="sr-only">Loading activity</span>
        </div>
      )}

      {status === 'error' && (
        <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
          <p className="text-[15px] text-ink-soft">
            Couldn&apos;t load your activity. Nothing shown here was approved or stopped while
            we were offline.
          </p>
          <button
            type="button"
            onClick={retry}
            className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
          >
            Try again
          </button>
        </div>
      )}

      {status === 'ready' && groups.length === 0 && (
        <p className="text-[15px] text-ink-muted">Nothing here yet.</p>
      )}

      {status === 'ready' &&
        groups.map((group) => (
          <div key={group.key}>
            <p className="mb-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
              {group.label}
            </p>
            <div className="flex flex-col gap-1">
              {group.rows.map((decision) => (
                <DecisionMark
                  key={decision.authorization_id}
                  decision={decision}
                  onClick={() => selectDecision(decision)}
                />
              ))}
            </div>
          </div>
        ))}
    </div>
  )
}
