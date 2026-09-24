import type { Decision } from '../api/types'
import { formatChf } from '../lib/money'
import { recentDecisions } from '../lib/spend'
import type { FilterId } from '../screens/Activity/Activity'

// Same circle/rounded-square/diamond shape language as OrderCapLeashMeter's
// dots (D-042-era accessibility fix) — a decision is never color-only here
// either.
const AMOUNT_STYLE: Record<
  'approved' | 'stopped' | 'uncertain',
  { label: string; shape: string; color: string }
> = {
  approved: { label: 'Approved', shape: 'size-[12px] rounded-full', color: 'bg-approved-on-ink' },
  stopped: { label: 'Stopped', shape: 'size-[12px] rounded-[2px]', color: 'bg-stopped-on-ink' },
  uncertain: {
    label: 'Uncertain',
    shape: 'size-[9px] rounded-[1px] rotate-45',
    color: 'bg-asked-on-ink',
  },
}

/**
 * DESIGN.md `OverviewHero` — first card on Home. Leads with the
 * "protection" story, not a neutral tally (D-055): the hero number is how
 * many purchases needed a closer look (stopped or uncertain), not a CHF
 * total — Activity's own filter tiles already cover counts and this
 * card's own tiles below already cover amounts, so the headline's job is
 * to answer "is my agent behaving" in one glance. An expired request still
 * counts as Uncertain (D-041), never its own bucket. No limits or meters
 * here — those live per-card on `LeashMeter`.
 */
export function OverviewHero({
  decisions,
  onOpenActivity,
}: {
  decisions: Decision[]
  onOpenActivity: (filter: FilterId) => void
}) {
  const recent = recentDecisions(decisions, 7)
  const approvedRecent = recent.filter((d) => d.decision === 'approved')
  const stoppedRecent = recent.filter((d) => d.decision === 'stopped')
  const uncertainRecent = recent.filter((d) => d.decision === 'uncertain')
  const riskyCount = stoppedRecent.length + uncertainRecent.length
  const approvedCount = approvedRecent.length

  const totals = {
    approved: approvedRecent.reduce((sum, d) => sum + d.billing_amount_chf, 0),
    stopped: stoppedRecent.reduce((sum, d) => sum + d.billing_amount_chf, 0),
    uncertain: uncertainRecent.reduce((sum, d) => sum + d.billing_amount_chf, 0),
  }

  let narrative: string
  if (riskyCount === 0) {
    narrative =
      recent.length === 0
        ? 'No purchases yet this week.'
        : `Nothing needed your attention — all ${recent.length === 1 ? '1 purchase' : `${recent.length} purchases`} went through your rules cleanly.`
  } else if (approvedCount > 0) {
    narrative = `${approvedCount === 1 ? '1 other purchase' : `${approvedCount} other purchases`} went through automatically — nothing needed you.`
  } else {
    narrative = 'No other purchases went through this week.'
  }

  return (
    <div className="rounded-hero bg-ink p-6">
      <p className="text-[13px] font-medium text-on-ink-muted">
        Processed by your rules · last 7 days
      </p>
      <p className="mt-2 font-display text-[46px] leading-none font-bold text-on-ink tabular-nums">
        {riskyCount}
      </p>
      <p className="mt-1 text-[15px] font-semibold text-on-ink">
        {riskyCount === 1 ? 'risky purchase caught' : 'risky purchases caught'}
      </p>
      <p className="mt-2 text-[13px] text-on-ink-soft">{narrative}</p>

      <div className="mt-5 grid grid-cols-3 gap-2 border-t border-on-ink-rule pt-4">
        {(['approved', 'stopped', 'uncertain'] as const).map((key) => {
          const { label, shape, color } = AMOUNT_STYLE[key]
          return (
            <button
              key={key}
              type="button"
              onClick={() => onOpenActivity(key)}
              className="flex min-h-11 flex-col items-center gap-1.5 rounded-tile px-1 py-2"
            >
              <span className={`${shape} ${color}`} />
              <span className="w-full truncate text-center font-display text-[17px] font-bold text-on-ink tabular-nums">
                {formatChf(totals[key])}
              </span>
              <span className="text-[11px] font-medium text-on-ink-muted">{label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
