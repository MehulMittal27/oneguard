import type { Decision, UncertainOutcome } from '../api/types'
import { formatChf } from '../lib/money'
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
}: {
  decision: Decision
  onClick?: () => void
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
      className="flex min-h-16 w-full items-center gap-4 rounded-row px-5 py-3 text-left"
    >
      <span
        className={`flex size-[38px] shrink-0 items-center justify-center rounded-full ${discBg} ${fg}`}
      >
        {isUncertain ? (
          <HelpCircleIcon size={18} strokeWidth={2.2} />
        ) : decision.decision === 'approved' ? (
          <CheckIcon size={18} strokeWidth={2.4} />
        ) : (
          <CrossIcon size={18} strokeWidth={2.4} />
        )}
      </span>
      <span className="min-w-0 flex-1">
        {/* Merchant name is untrusted merchant text — plain text node only. */}
        <span className="block truncate text-[15px] font-semibold text-ink">
          {decision.merchant.name}
        </span>
        <span className="block truncate text-[13px] text-ink-muted">{decision.message}</span>
      </span>
      <span className="shrink-0 text-right">
        <span className="block text-[15px] font-semibold text-ink tabular-nums">
          {formatChf(decision.billing_amount_chf)}
        </span>
        <span className={`block text-[12px] font-semibold ${fg}`}>{label}</span>
      </span>
    </Tag>
  )
}
