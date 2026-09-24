import { useState } from 'react'
import type { Decision } from '../../api/types'
import { DecisionMark } from '../../components/DecisionMark'
import { EarlierRuns } from '../../components/EarlierRuns'
import { dateKey, formatShortDate } from '../../lib/datetime'
import { splitByRun } from '../../lib/runs'
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

/**
 * The selected filter fills with its own decision tint; "All" is neutral ink,
 * since it is not a decision. Never colour-only — outline vs filled, medium vs
 * semibold, label and count always in text. Each pair clears 4.5:1 (approved
 * 4.65, stopped 5.61, uncertain 5.61 on their own tints).
 */
const FILTER_STYLE: Record<FilterId, { rest: string; active: string }> = {
  all: {
    rest: 'border-hairline bg-surface text-ink-muted',
    active: 'border-ink bg-ink text-on-ink',
  },
  approved: {
    rest: 'border-hairline bg-surface text-ink-muted',
    active: 'border-approved bg-approved-tint text-approved',
  },
  stopped: {
    rest: 'border-hairline bg-surface text-ink-muted',
    active: 'border-stopped bg-stopped-tint text-stopped',
  },
  uncertain: {
    rest: 'border-hairline bg-surface text-ink-muted',
    active: 'border-asked bg-asked-tint text-asked',
  },
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

  // Each card's newest run is the list; older runs fold under "Earlier runs"
  // (lib/runs.ts). Counts are of what the list shows.
  const { current, earlier } = splitByRun(decisions)
  const matches = (d: Decision) => filter === 'all' || d.decision === filter
  const byNewest = (a: Decision, b: Decision) => b.occurred_at.localeCompare(a.occurred_at)

  const counts = {
    all: current.length,
    approved: current.filter((d) => d.decision === 'approved').length,
    stopped: current.filter((d) => d.decision === 'stopped').length,
    uncertain: current.filter((d) => d.decision === 'uncertain').length,
  }

  const filtered = current.filter(matches).sort(byNewest)
  const earlierRuns = earlier
    .map((run) => ({ ...run, decisions: run.decisions.filter(matches).sort(byNewest) }))
    .filter((run) => run.decisions.length > 0)

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

      {/*
        Full-bleed scroller: the negative margin lets a chip run to the screen
        edge while `px-8` keeps the first and last one aligned with the rest of
        the page. `min-w-0` is what actually makes it scroll — without it the row
        is a flex item at its automatic minimum size, so it grows to fit its
        chips and pushes them off-screen instead of overflowing inside itself.
      */}
      <div
        className="scrollbar-none -mx-8 flex min-w-0 gap-2 overflow-x-auto px-8"
        role="tablist"
        aria-label="Filter by decision"
      >
        {FILTERS.map(({ id, label }) => {
          const isActive = id === filter
          const style = FILTER_STYLE[id]
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setFilter(id)}
              className={`flex min-h-11 shrink-0 items-center gap-1.5 rounded-pill border-2 px-4 text-[13px] transition-colors ${
                isActive ? `${style.active} font-semibold` : `${style.rest} font-medium`
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
        <p className="text-[15px] text-ink-muted">
          {earlierRuns.length > 0 ? 'Nothing here in the latest run.' : 'Nothing here yet.'}
        </p>
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

      {status === 'ready' && <EarlierRuns runs={earlierRuns} onSelect={selectDecision} />}
    </div>
  )
}
