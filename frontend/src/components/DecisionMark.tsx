import type { Decision, UncertainOutcome } from '../api/types'
import { formatChf } from '../lib/money'
import { decisionReasonLabel } from '../lib/reasonCodes'
import { formatShortDate, formatTime } from '../lib/datetime'
import { CheckIcon, CrossIcon, HelpCircleIcon } from './icons/lucide'

const STYLE: Record<'approved' | 'stopped', { label: string; discBg: string; fg: string }> = {
  approved: { label: 'Approved', discBg: 'bg-approved-tint', fg: 'text-approved' },
  stopped: { label: 'Stopped', discBg: 'bg-stopped-tint', fg: 'text-stopped' },
}

// Only 3 categories (D-041) — a step-up's human answer or an expiry is a
// status of Uncertain, never its own row. The icon stays help-circle for
// every uncertain sub-status (never a new category); only the disc
// background/text color and the status word change with it.
const UNCERTAIN_STYLE: Record<UncertainOutcome, { label: string; discBg: string; fg: string }> = {
  pending: { label: 'Uncertain · waiting', discBg: 'bg-asked-tint', fg: 'text-asked' },
  expired: { label: 'Uncertain · expired', discBg: 'bg-surface-expired', fg: 'text-ink-muted' },
  approved: { label: 'Uncertain · approved by you', discBg: 'bg-approved-tint', fg: 'text-approved' },
  declined: { label: 'Uncertain · blocked by you', discBg: 'bg-stopped-tint', fg: 'text-stopped' },
}

/** DESIGN.md `DecisionMark`: one per activity row, and the entry point to decision detail (slice 7). */
export function DecisionMark({
  decision,
  onClick,
  timestamp = 'none',
  compact = false,
  tagOnly = false,
}: {
  decision: Decision
  onClick?: () => void
  timestamp?: 'none' | 'time' | 'day-time'
  compact?: boolean
  tagOnly?: boolean
}) {
  const isUncertain = decision.decision === 'uncertain'
  const { label, discBg, fg } =
    decision.decision === 'uncertain'
      ? UNCERTAIN_STYLE[decision.uncertain_outcome ?? 'pending']
      : STYLE[decision.decision]
  const Tag = onClick ? 'button' : 'div'

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-label={tagOnly ? `${decision.merchant.name}; ${label}; ${formatChf(decision.billing_amount_chf)}` : undefined}
      className={`flex w-full items-center rounded-row text-left ${tagOnly ? 'min-h-11 justify-start px-3 py-2' : 'min-h-16 gap-4 px-5 py-3'}`}
    >
      {!tagOnly && <span
        className={`flex size-[38px] shrink-0 items-center justify-center rounded-full ${discBg} ${fg}`}
      >
        {isUncertain ? (
          <HelpCircleIcon size={18} strokeWidth={2.2} />
        ) : decision.decision === 'approved' ? (
          <CheckIcon size={18} strokeWidth={2.4} />
        ) : (
          <CrossIcon size={18} strokeWidth={2.4} />
        )}
      </span>}
      <span className="min-w-0 flex-1">
        {/* Merchant name is untrusted merchant text — plain text node only. */}
        <span className={`${tagOnly ? 'sr-only' : 'block truncate'} text-[15px] font-semibold text-ink`}>
          {decision.merchant.name}
          {timestamp !== 'none' && (
            <span className="font-normal text-ink-muted">
              {' · '}
              {timestamp === 'time'
                ? formatTime(decision.occurred_at)
                : `${formatShortDate(decision.occurred_at).split(' ')[0]} ${formatTime(decision.occurred_at)}`}
            </span>
          )}
        </span>
        {!compact && !tagOnly && <span className="block truncate text-[13px] text-ink-muted">{decision.message}</span>}
        <span
          className={`mt-1 inline-flex w-fit max-w-full whitespace-normal break-words rounded-[6px] px-2 py-1 text-[11px] leading-tight font-semibold ${
            decision.decision === 'approved'
              ? 'bg-approved-tint text-approved'
              : decision.decision === 'stopped'
                ? 'bg-stopped-tint text-stopped'
                : decision.uncertain_outcome === 'approved'
                  ? 'bg-approved-tint text-approved'
                  : decision.uncertain_outcome === 'declined'
                    ? 'bg-stopped-tint text-stopped'
                    : decision.uncertain_outcome === 'pending'
                      ? 'bg-asked-tint text-asked'
                      : 'bg-surface-expired text-ink-muted'
          }`}
        >
          {decision.reason_codes.length > 0
            ? decisionReasonLabel(
                decision.reason_codes[0],
                decision.evidence.map((item) => item.detail),
              )
            : 'Reviewed'}
        </span>
      </span>
      {!tagOnly && <span className="shrink-0 text-right">
        <span className="block text-[15px] font-semibold text-ink tabular-nums">
          {formatChf(decision.billing_amount_chf)}
        </span>
        <span className={`block text-[12px] font-semibold ${fg}`}>{label}</span>
      </span>}
    </Tag>
  )
}
