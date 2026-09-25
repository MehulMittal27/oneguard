import { useState } from 'react'
import type { Customer } from '../api/types'
import { getDecisions } from '../api/decisions'
import { usePolling } from '../lib/usePolling'
import { DECISIONS_POLL_SECONDS } from '../config'
import { groupByRun } from '../lib/runs'
import { formatRealTime } from '../lib/datetime'
import { DecisionRow } from '../components/DecisionRow'
import { EmptyState, ErrorState, Spinner } from '../components/StateViews'

type Filter = 'all' | 'approved' | 'stopped' | 'uncertain'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'approved', label: 'Approved' },
  { id: 'stopped', label: 'Declined' },
  { id: 'uncertain', label: 'Step-up' },
]

/**
 * The full "check every purchase decision, with the reason" view (C6),
 * grouped by run so a re-run of the same scenario doesn't read as duplicates
 * — same idea as `frontend/src/lib/runs.ts`, simplified per `lib/runs.ts`'s note.
 */
export function DecisionsScreen({ customers }: { customers: Customer[] }) {
  // `customers` arrives asynchronously (App's own C12 read) and may still be
  // empty on this screen's first render, so the picker's value is derived
  // rather than copied into state: an explicit pick always wins, and it
  // falls back to the first customer once the list lands.
  const [manualCustomerId, setManualCustomerId] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const customerId = manualCustomerId || customers[0]?.customer_id || ''

  const { data: decisions, error, loading, refresh } = usePolling(
    () => (customerId ? getDecisions(customerId) : Promise.resolve([])),
    DECISIONS_POLL_SECONDS,
    [customerId],
  )

  const filtered = (decisions ?? []).filter((d) => filter === 'all' || d.decision === filter)
  const { grouped, unattached } = groupByRun(filtered)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[20px] font-semibold text-ink">Decisions</h1>
        <p className="text-[13px] text-ink-muted">Every purchase the engine has decided for a customer, with the full evidence trail (C6).</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <select
          value={customerId}
          onChange={(e) => setManualCustomerId(e.target.value)}
          className="rounded-tile border border-border-quiet bg-surface px-3 py-2 text-[13px] text-ink outline-none focus:border-cord-accent"
        >
          {customers.length === 0 && <option value="">No customers yet</option>}
          {customers.map((c) => (
            <option key={c.customer_id} value={c.customer_id}>
              {c.name} ({c.customer_id}){c.live ? ' · live' : ''}
            </option>
          ))}
        </select>
        <div className="flex gap-1 rounded-pill bg-surface-sunken p-1 text-[12.5px] font-semibold">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`rounded-pill px-3 py-1.5 ${filter === f.id ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading && !decisions && <Spinner />}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {decisions && filtered.length === 0 && <EmptyState>No decisions match this filter yet.</EmptyState>}

      <div className="space-y-6">
        {grouped.map((run) => (
          <section key={run.runId} className="space-y-2">
            <h2 className="text-[12px] font-semibold tracking-[0.04em] text-ink-muted uppercase">
              Run {run.runId} · card {run.cardId}
              {run.startedAt && <span className="ml-2 font-normal normal-case tabular-nums">started {formatRealTime(run.startedAt)}</span>}
            </h2>
            <div className="space-y-2">
              {run.decisions.map((d) => (
                <DecisionRow key={d.authorization_id} decision={d} />
              ))}
            </div>
          </section>
        ))}
        {unattached.length > 0 && (
          <section className="space-y-2">
            {grouped.length > 0 && <h2 className="text-[12px] font-semibold tracking-[0.04em] text-ink-muted uppercase">Not tied to a run</h2>}
            <div className="space-y-2">
              {unattached.map((d) => (
                <DecisionRow key={d.authorization_id} decision={d} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
