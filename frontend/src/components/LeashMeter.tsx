import type { Decision, DecisionOutcome } from '../api/types'
import { formatChf } from '../lib/money'

// Only 3 categories (D-041) — 'uncertain' stays ochre regardless of
// pending/expired/resolved sub-status, matching DecisionMark. Shape (not
// just color) distinguishes them too, reusing DESIGN.md's own OverviewHero
// convention (circle/rounded-square/diamond) so this dot ruler isn't
// color-only (ROADMAP.md slice 9 accessibility pass).
const DOT_STYLE: Record<DecisionOutcome, string> = {
  approved: 'bg-approved rounded-full',
  stopped: 'bg-stopped rounded-[2px]',
  uncertain: 'bg-asked rounded-[1px] rotate-45',
}

/**
 * DESIGN.md `LeashMeter` — the horizontal track the mockup draws: solid fill
 * for spend, dashed ghost for anything stepped up and waiting, marker at the
 * limit. Replaces the radial gauge of D-061.
 *
 * Only approved purchases fill it; pending is always the ghost, never solid
 * (hard rule: step_up never renders as approved). The label deliberately does
 * not say "processed" — a period limit governs spend, so a stopped or uncertain
 * purchase must not move this bar.
 */
/**
 * The window a period limit is measured over, in the customer's own words. The
 * length comes from the mandate (`usage.period_days`, or the check wording),
 * so a 14- or 30-day policy stops being described as "this week".
 */
function periodLabel(days: number): string {
  if (days === 7) return 'This week'
  return `Last ${days} days`
}

/** The same window as a sentence fragment: "CHF 45.50 left this week". */
function periodPhrase(days: number): string {
  return days === 7 ? 'this week' : `in the last ${days} days`
}

export function PeriodLeashMeter({
  limitChf,
  spentChf,
  pendingChf,
  days,
}: {
  limitChf: number
  spentChf: number
  pendingChf: number
  days: number
}) {
  const remaining = Math.max(0, limitChf - spentChf)
  const spentFraction = Math.min(1, spentChf / limitChf)
  const pendingFraction = Math.min(1 - spentFraction, pendingChf / limitChf)

  return (
    <div>
      <div className="flex justify-between text-[13px] text-ink-soft tabular-nums">
        <span>
          {periodLabel(days)}: {formatChf(spentChf)}
        </span>
        <span>of {formatChf(limitChf)}</span>
      </div>

      <div className="relative mt-2 h-6">
        <div className="absolute inset-x-0 top-[5px] h-3.5 rounded-meter bg-surface-active" />
        {spentFraction > 0 && (
          <div
            className="absolute top-[5px] h-3.5 rounded-meter bg-leash-fill"
            style={{ width: `${spentFraction * 100}%` }}
          />
        )}
        {/* A stepped-up purchase is a reservation, never spend: it is drawn as
            a dashed ghost after the solid fill and is named in the legend
            below, so it can never be mistaken for an approval. */}
        {pendingFraction > 0 && (
          <div
            className="absolute top-[5px] box-border h-3.5 rounded-meter border-2 border-dashed border-asked bg-asked-hatch"
            style={{
              left: `${spentFraction * 100}%`,
              width: `${Math.max(pendingFraction * 100, 3)}%`,
            }}
          />
        )}
        <div
          aria-hidden="true"
          className="absolute top-0 right-0 h-6 w-1 rounded-[2px] bg-leash-limit"
        />
      </div>

      <div className="mt-2 flex flex-col gap-1 text-[12px] text-ink-muted">
        <span>
          {formatChf(remaining)} left {periodPhrase(days)}
        </span>
        {pendingChf > 0 && (
          <span className="text-asked">
            {formatChf(pendingChf)} waiting for you — not spent unless you approve it
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * DESIGN.md's explicit fallback for a per-order-cap-only policy (no period
 * limit): "spec-only, not illustrated in the source mockups" — one dot per
 * recent order (colored by its decision) along a scale capped at 1.5x the
 * limit, with the limit itself drawn as a marker line.
 */
export function OrderCapLeashMeter({
  capChf,
  decisions,
}: {
  capChf: number
  decisions: Decision[]
}) {
  const scaleMax = capChf * 1.5
  const capPosition = (capChf / scaleMax) * 100

  return (
    <div>
      <p className="font-display text-[46px] leading-none font-bold text-ink tabular-nums">
        {formatChf(capChf)}
      </p>
      <p className="mt-1 text-[13px] text-ink-muted">per order — no rolling total on this card</p>

      {/*
        The dots are centred on their position, so the track keeps half a dot of
        room at each end — otherwise the first and last are cut off by the card.
        Position clamps at 100%: it used to run to 108%, which put an order above
        the scale outside the card entirely. The scale already ends at 1.5x the
        cap, so the far right reads as "off the end" without leaving the card.
      */}
      <div className="mx-[5px] mt-6">
        <div className="relative h-[14px] rounded-meter bg-surface-active">
          <div
            className="absolute inset-y-0 w-[4px] rounded-meter bg-leash-limit"
            style={{ left: `${capPosition}%` }}
          />
          {decisions.map((d) => (
            <span
              key={d.authorization_id}
              className={`absolute top-1/2 size-[10px] -translate-x-1/2 -translate-y-1/2 border-2 border-surface ${DOT_STYLE[d.decision]}`}
              style={{ left: `${Math.min(1, d.billing_amount_chf / scaleMax) * 100}%` }}
              title={`${formatChf(d.billing_amount_chf)} — ${d.decision}`}
            />
          ))}
        </div>
      </div>

      <div className="mt-2 flex justify-between text-[13px] text-ink-soft">
        <span>Recent orders on this card</span>
        <span>Cap {formatChf(capChf)}</span>
      </div>
    </div>
  )
}
