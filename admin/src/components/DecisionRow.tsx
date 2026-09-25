import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { Decision } from '../api/types'
import { formatChf } from '../lib/money'
import { formatSimTime } from '../lib/datetime'
import { Badge } from './Badge'
import { DecisionDetail } from './DecisionDetail'

const OUTCOME_LABEL: Record<'approved' | 'stopped', { label: string; tone: 'approved' | 'stopped' }> = {
  approved: { label: 'Approved', tone: 'approved' },
  stopped: { label: 'Declined', tone: 'stopped' },
}

const UNCERTAIN_LABEL: Record<NonNullable<Decision['uncertain_outcome']>, string> = {
  pending: 'Waiting for customer',
  expired: 'Expired · declined',
  approved: 'Customer approved',
  declined: 'Customer declined',
}

/** One decision row: the same three-way approve/decline/step-up vocabulary as
 * `frontend/src/components/DecisionMark.tsx`, expandable into the full evidence
 * trail (root CLAUDE.md rule 10 — a decision with no reason is a bug). */
export function DecisionRow({ decision }: { decision: Decision }) {
  const [open, setOpen] = useState(false)
  const tone = decision.decision === 'uncertain' ? 'asked' : OUTCOME_LABEL[decision.decision].tone
  const label =
    decision.decision === 'uncertain'
      ? UNCERTAIN_LABEL[decision.uncertain_outcome ?? 'pending']
      : OUTCOME_LABEL[decision.decision].label

  return (
    <div className="rounded-row border border-hairline bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-4 px-5 py-3 text-left"
      >
        {open ? <ChevronDown size={16} className="shrink-0 text-ink-muted" /> : <ChevronRight size={16} className="shrink-0 text-ink-muted" />}
        <Badge tone={tone}>{label}</Badge>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[14px] font-semibold text-ink">{decision.merchant.name}</span>
          <span className="block truncate text-[12px] text-ink-muted">{decision.message}</span>
        </span>
        {decision.injection_flag && <Badge tone="stopped">Injection flagged</Badge>}
        <span className="shrink-0 text-right">
          <span className="block text-[14px] font-semibold text-ink tabular-nums">{formatChf(decision.billing_amount_chf)}</span>
          <span className="block text-[11px] text-ink-muted tabular-nums">{formatSimTime(decision.occurred_at)}</span>
        </span>
      </button>
      {open && <DecisionDetail decision={decision} />}
    </div>
  )
}
