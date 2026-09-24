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

// Semicircle geometry shared by every arc segment below — a fixed track
// (cx, cy, r) in a 220x112 viewBox, parametrized 180deg (left end) to
// 360deg (right end) through the top, so `fraction` maps linearly onto
// the same arc every time.
const ARC_CX = 110
const ARC_CY = 100
const ARC_R = 84

function arcPoint(deg: number) {
  const rad = (deg * Math.PI) / 180
  return { x: ARC_CX + ARC_R * Math.cos(rad), y: ARC_CY + ARC_R * Math.sin(rad) }
}

function arcPath(fromDeg: number, toDeg: number) {
  const start = arcPoint(fromDeg)
  const end = arcPoint(toDeg)
  const largeArc = toDeg - fromDeg > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${ARC_R} ${ARC_R} 0 ${largeArc} 1 ${end.x} ${end.y}`
}

function degForFraction(fraction: number) {
  return 180 + fraction * 180
}

/**
 * DESIGN.md `LeashMeter` — light-card variant (Card detail is not an ink
 * card), drawn as a radial collar gauge rather than a linear bar. Same
 * underlying data as before: solid spent arc, dashed "ghost" arc for
 * pending, a fixed limit marker at the end of the track. Only approved
 * purchases fill the meter; pending is always the dashed ghost, never
 * solid (hard rule: step_up never renders as approved) — the arc-based
 * layout changes nothing about that rule, only how it's drawn.
 */
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
  const spentEndDeg = degForFraction(spentFraction)
  const pendingEndDeg = degForFraction(Math.min(1, spentFraction + pendingFraction))
  const limitTick = arcPoint(359.9)
  const remainingLabel = formatChf(remaining)
  // The figure sits between the arc's two ends; a long amount steps down a
  // size so it never runs into the stroke.
  const remainingSize = remainingLabel.length > 9 ? 'text-[26px]' : 'text-[30px]'

  return (
    <div>
      <div className="relative mx-auto max-w-[280px]">
        <svg viewBox="0 0 220 112" className="w-full" aria-hidden="true">
          <path
            d={arcPath(180, 360)}
            fill="none"
            strokeWidth={14}
            strokeLinecap="round"
            className="stroke-surface-active"
          />
          {pendingFraction > 0 && (
            <path
              d={arcPath(spentEndDeg, pendingEndDeg)}
              fill="none"
              strokeWidth={14}
              strokeLinecap="round"
              strokeDasharray="1.5 8"
              className="stroke-asked-border"
            />
          )}
          {spentFraction > 0 && (
            <path
              d={arcPath(180, spentEndDeg)}
              fill="none"
              strokeWidth={14}
              strokeLinecap="round"
              className="stroke-leash-fill"
            />
          )}
          {/* Limit marker, same role as the linear meter's dedicated
              4px edge — a fixed tick at the end of the track, not part
              of the spent/pending data. */}
          <line
            x1={limitTick.x}
            y1={limitTick.y - 9}
            x2={limitTick.x}
            y2={limitTick.y + 9}
            strokeWidth={4}
            strokeLinecap="round"
            className="stroke-leash-limit"
          />
        </svg>
        <div className="absolute inset-x-0 bottom-0 flex flex-col items-center">
          <p className={`font-display ${remainingSize} leading-none font-bold text-ink tabular-nums`}>
            {remainingLabel}
          </p>
          <p className="mt-1 text-[13px] text-ink-muted">left in the last {days} days</p>
        </div>
      </div>

      <div className="mt-2 flex justify-between text-[13px] text-ink-soft">
        <span>{formatChf(spentChf)} spent</span>
        <span>Limit {formatChf(limitChf)}</span>
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

      <div className="relative mt-6 h-[14px] rounded-meter bg-surface-active">
        <div
          className="absolute inset-y-0 w-[4px] rounded-meter bg-leash-limit"
          style={{ left: `${capPosition}%` }}
        />
        {decisions.map((d) => (
          <span
            key={d.authorization_id}
            className={`absolute top-1/2 size-[10px] -translate-x-1/2 -translate-y-1/2 border-2 border-surface ${DOT_STYLE[d.decision]}`}
            style={{ left: `${Math.min(1.08, d.billing_amount_chf / scaleMax) * 100}%` }}
            title={`${formatChf(d.billing_amount_chf)} — ${d.decision}`}
          />
        ))}
      </div>

      <div className="mt-2 flex justify-between text-[13px] text-ink-soft">
        <span>Recent orders on this card</span>
        <span>Cap {formatChf(capChf)}</span>
      </div>
    </div>
  )
}
