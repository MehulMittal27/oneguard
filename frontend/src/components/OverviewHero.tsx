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
  approved: { label: 'Approved', shape: 'size-[12px] rounded-full', color: 'bg-approved' },
  stopped: { label: 'Stopped', shape: 'size-[12px] rounded-[2px]', color: 'bg-stopped' },
  uncertain: {
    label: 'Uncertain',
    shape: 'size-[9px] rounded-[1px] rotate-45',
    color: 'bg-asked',
  },
}

/**
 * DESIGN.md `OverviewHero` — first card on Home: the total amount processed, and
 * three counts under it.
 *
 * That total is not spend — it includes stopped and uncertain purchases, hence
 * "processed" and "proposed". The counts add up to the caption's number, and an
 * expired request counts as Uncertain (D-041), never its own bucket. Supersedes
 * D-055, which made the headline a count of risky purchases instead.
 *
 * Don't reintroduce `-on-ink` tokens here: this is a white card now, and they
 * are tuned for a dark ground.
 */
export function OverviewHero({
  decisions,
  onOpenActivity,
}: {
  decisions: Decision[]
  onOpenActivity: (filter: FilterId) => void
}) {
  const recent = recentDecisions(decisions, 7)
  const counts = {
    approved: recent.filter((d) => d.decision === 'approved').length,
    stopped: recent.filter((d) => d.decision === 'stopped').length,
    uncertain: recent.filter((d) => d.decision === 'uncertain').length,
  }
  const processedChf = recent.reduce((sum, d) => sum + d.billing_amount_chf, 0)

  return (
    <div className="rounded-hero border border-hairline bg-surface p-6">
      <p className="text-[11px] font-semibold tracking-[0.09em] text-cord-accent uppercase">
        Processed by your rules · last 7 days
      </p>
      <p className="mt-4 font-display text-[40px] leading-none font-bold tracking-[-0.02em] text-ink tabular-nums">
        {formatChf(processedChf)}
      </p>
      <p className="mt-2 text-[15px] text-ink-muted">
        {recent.length === 1
          ? '1 purchase proposed by your agent'
          : `${recent.length} purchases proposed by your agent`}
      </p>

      <div className="mt-5 grid grid-cols-3 gap-2 border-t border-hairline pt-4">
        {(['approved', 'stopped', 'uncertain'] as const).map((key) => {
          const { label, shape, color } = AMOUNT_STYLE[key]
          return (
            <button
              key={key}
              type="button"
              onClick={() => onOpenActivity(key)}
              className="flex min-h-11 flex-col items-start gap-1 rounded-tile py-1 text-left"
            >
              <span className="flex items-center gap-1.5 text-[13px] text-ink-muted">
                <span className={`shrink-0 ${shape} ${color}`} />
                {label}
              </span>
              <span className="font-display text-[30px] leading-[1.1] font-bold text-ink tabular-nums">
                {counts[key]}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
