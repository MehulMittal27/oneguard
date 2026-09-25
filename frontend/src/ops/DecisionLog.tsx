import { useMemo, useState } from 'react'
import type { Decision } from '../api/types'
import { LOG_FILTERS, exportFileName, filterCounts, filterDecisions, type LogFilter, type Verification } from '../lib/opsConsole'
import { DecisionStream } from './DecisionStream'
import { BUTTON_SECONDARY, TEXT_M, TEXT_S } from './style'

/**
 * The Decision log tab: the current run's full stream, filtered by outcome and
 * searched by shop, each row expandable to its evidence, counterfactual, what the
 * agent was told and its signed receipt. "Export JSON" saves the rows shown, as
 * C6 served them (operator view).
 */
export function DecisionLog({
  decisions,
  expanded,
  onToggle,
  onOpenRaw,
  scenarioId,
  runKind,
}: {
  decisions: Decision[] | null
  expanded: ReadonlySet<string>
  onToggle: (authorizationId: string) => void
  onOpenRaw: (decision: Decision, receiptCheck: Verification | null) => void
  scenarioId: string | null
  runKind: string | null
}) {
  const [filter, setFilter] = useState<LogFilter>('all')
  const [query, setQuery] = useState('')
  const counts = useMemo(() => filterCounts(decisions ?? []), [decisions])
  const shown = useMemo(
    () => (decisions === null ? null : filterDecisions(decisions, filter, query)),
    [decisions, filter, query],
  )

  function exportJson() {
    if (!shown) return
    const blob = new Blob([JSON.stringify(shown, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = exportFileName(scenarioId, runKind)
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
          {LOG_FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`${TEXT_M} inline-flex min-h-9 items-center gap-2 rounded-pill border px-4 font-semibold transition-colors ${
                filter === f.id
                  ? 'border-[var(--ops-gold)] bg-[var(--ops-gold)] text-ink'
                  : 'border-border-quiet bg-surface text-ink-muted hover:border-[var(--ops-gold)] hover:text-ink'
              }`}
            >
              {f.label}
              <span className={`${TEXT_S} tabular-nums`}>{counts[f.id]}</span>
            </button>
          ))}
        </div>
        <label className="ml-auto flex min-w-[240px] items-center">
          <span className="sr-only">Search by shop</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by shop"
            className={`${TEXT_M} min-h-10 w-full rounded-button border border-border-quiet bg-surface px-4 text-ink placeholder:text-ink-muted focus:border-[var(--ops-gold)] focus:outline-none`}
          />
        </label>
        <button type="button" className={BUTTON_SECONDARY} disabled={!shown || shown.length === 0} onClick={exportJson}>
          Export JSON
        </button>
      </div>
      <DecisionStream
        decisions={shown}
        expanded={expanded}
        onToggle={onToggle}
        onOpenRaw={onOpenRaw}
        emptyText={decisions && decisions.length > 0 ? 'No decision matches this filter or search.' : 'No decision in this run yet.'}
      />
    </div>
  )
}
