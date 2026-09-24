import { useState } from 'react'
import type { Decision } from '../api/types'
import { formatRunStart, type EarlierRun } from '../lib/runs'
import { DecisionMark } from './DecisionMark'
import { ChevronDownIcon } from './icons/lucide'

/**
 * A card's older runs, under "Earlier runs (n)" (`../../docs/api-contract.md`
 * §6 item 14). Each run is one row with its start time, folded until the
 * customer opens it, so a replayed scenario never reads as duplicate purchases
 * next to the newest run.
 */
export function EarlierRuns({
  runs,
  onSelect,
}: {
  runs: EarlierRun[]
  onSelect: (decision: Decision) => void
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())

  if (runs.length === 0) return null

  function toggle(key: string) {
    setOpen((current) => {
      const next = new Set(current)
      if (!next.delete(key)) next.add(key)
      return next
    })
  }

  return (
    <section className="flex flex-col gap-3">
      <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
        Earlier runs ({runs.length})
      </p>
      <div className="flex flex-col gap-2">
        {runs.map((run) => {
          const isOpen = open.has(run.key)
          const count = run.decisions.length
          return (
            <div key={run.key} className="rounded-row border border-hairline bg-surface">
              {/* `px-2` + the row's `px-5`: the same insets as the purchase rows
                  below (`p-2` + DecisionMark's `px-5`), so the start time, the
                  chevron and the rows share one left and one right edge. */}
              <div className="px-2">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggle(run.key)}
                  className="flex min-h-16 w-full items-center gap-3 px-5 py-3 text-left"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[15px] font-semibold text-ink">
                      {run.startedAt ? `Started ${formatRunStart(run.startedAt)}` : 'Earlier run'}
                    </span>
                    <span className="block truncate text-[13px] text-ink-muted">
                      {count === 1 ? '1 purchase' : `${count} purchases`} · Card {run.cardId}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 text-ink-muted transition-transform ${isOpen ? 'rotate-180' : ''}`}
                  >
                    <ChevronDownIcon size={20} strokeWidth={2} />
                  </span>
                </button>
              </div>
              {isOpen && (
                <div className="flex flex-col gap-1 border-t border-hairline p-2">
                  {run.decisions.map((decision) => (
                    <DecisionMark
                      key={decision.authorization_id}
                      decision={decision}
                      onClick={() => onSelect(decision)}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
