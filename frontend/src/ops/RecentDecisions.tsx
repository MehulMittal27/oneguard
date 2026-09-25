import type { Decision } from '../api/types'
import { formatTime } from '../lib/datetime'
import { formatChf } from '../lib/money'
import { isWaiting, outcomeBadge } from '../lib/opsConsole'
import { LINK, TEXT_M, TEXT_S, TONE_CLASS } from './style'

const SHOWN = 5

/**
 * Overview's strip: the current run's last five decisions (newest delivered
 * first), one line each. A row opens in the Decision log, expanded.
 */
export function RecentDecisions({
  decisions,
  onOpen,
  onOpenLog,
}: {
  decisions: Decision[] | null
  onOpen: (authorizationId: string) => void
  onOpenLog: () => void
}) {
  return (
    <section aria-label="Latest decisions" className="flex flex-col rounded-card border border-hairline bg-surface">
      <div className={`${TEXT_M} flex items-center justify-between gap-4 border-b border-hairline px-8 py-3`}>
        <h2 className="font-semibold text-ink">Latest decisions</h2>
        <button type="button" onClick={onOpenLog} className={LINK}>
          Open the decision log
        </button>
      </div>
      {decisions === null ? (
        <div className="flex flex-col gap-2 px-8 py-4" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-8 animate-pulse rounded-row bg-surface-sunken" />
          ))}
        </div>
      ) : decisions.length === 0 ? (
        <p className={`${TEXT_M} px-8 py-4 text-ink-muted`}>No decision in this run yet.</p>
      ) : (
        <ol>
          {decisions.slice(0, SHOWN).map((d) => {
            const badge = outcomeBadge(d)
            return (
              <li
                key={d.authorization_id}
                className={`border-b border-hairline last:border-b-0 ${isWaiting(d) ? 'bg-asked-tint/45 shadow-[inset_4px_0_0_var(--asked-dot)]' : ''}`}
              >
                <button
                  type="button"
                  onClick={() => onOpen(d.authorization_id)}
                  className={`${TEXT_M} grid w-full grid-cols-[56px_minmax(0,1fr)_auto_104px] items-center gap-x-5 px-8 py-2.5 text-left hover:bg-surface-sunken/60`}
                >
                  <span className="text-ink-muted tabular-nums">{formatTime(d.occurred_at)}</span>
                  {/* Shop name: untrusted merchant text, a plain text node. */}
                  <span className="truncate font-semibold text-ink">{d.merchant.name}</span>
                  <span className="text-right font-semibold whitespace-nowrap text-ink tabular-nums">
                    {formatChf(d.billing_amount_chf)}
                  </span>
                  <span className={`${TEXT_S} justify-self-end rounded-pill px-2.5 py-0.5 font-semibold ${TONE_CLASS[badge.tone]}`}>
                    {badge.label}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
