import type { Decision, EvidenceItem } from '../api/types'
import { formatChf } from '../lib/money'
import { reasonLabel } from '../lib/reasonCodes'
import { Badge } from './Badge'

const EVIDENCE_TONE: Record<EvidenceItem['outcome'], 'approved' | 'stopped' | 'asked' | 'neutral'> = {
  pass: 'approved',
  fail: 'stopped',
  uncertain: 'asked',
  info: 'neutral',
}

/**
 * Everything the judging pack asks for on one decision: permitted (evidence
 * sourced from `policy`), evidence, why (message + reason codes +
 * counterfactual), control (session / resolved_by) — `docs/api-contract.md`
 * §5. Merchant text (`item_details`, item and merchant names) renders as
 * plain text only, per root CLAUDE.md rule 2.
 */
export function DecisionDetail({ decision }: { decision: Decision }) {
  return (
    <div className="space-y-4 border-t border-hairline px-5 py-4 text-[13px]">
      <div>
        <p className="font-semibold text-ink">{decision.message}</p>
        {decision.counterfactual && <p className="mt-1 text-ink-muted">{decision.counterfactual}</p>}
        {decision.uncertainty?.note && <p className="mt-1 text-asked-ink">{decision.uncertainty.note}</p>}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {decision.reason_codes.map((code) => (
          <Badge key={code} tone="neutral">
            {reasonLabel(code)}
          </Badge>
        ))}
      </div>

      {decision.session && decision.session.trust !== 'normal' && (
        <p className="rounded-tile bg-asked-tint px-3 py-2 text-asked-ink">Session: {decision.session.trust} — {decision.session.note}</p>
      )}

      <div>
        <p className="mb-1.5 text-[11px] font-semibold tracking-[0.06em] text-ink-muted uppercase">Evidence</p>
        <div className="space-y-1.5">
          {decision.evidence.map((row, i) => (
            <div key={i} className="flex items-start gap-2.5 rounded-tile bg-surface-sunken px-3 py-2">
              <Badge tone={EVIDENCE_TONE[row.outcome]}>{row.outcome}</Badge>
              <span className="flex-1">
                <span className="font-medium text-ink">{row.rule}</span>
                <span className="text-ink-muted"> — {row.detail}</span>
                {row.source && <span className="ml-1 text-[11px] text-ink-muted">({row.source})</span>}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <p className="mb-1.5 text-[11px] font-semibold tracking-[0.06em] text-ink-muted uppercase">Cart — from the shop</p>
        <div className="space-y-1">
          {decision.items.map((item, i) => (
            <p key={i} className="text-ink-soft">
              {item.quantity}× <span className="text-ink">{item.item_name}</span> — {formatChf(item.unit_price)} {item.currency}
              {item.item_details && <span className="text-ink-muted"> · {item.item_details}</span>}
            </p>
          ))}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-ink-muted sm:grid-cols-4">
        <dt className="font-medium text-ink-soft">Returnable</dt>
        <dd className="tabular-nums">{decision.order_returnable}</dd>
        <dt className="font-medium text-ink-soft">Authorization</dt>
        <dd className="truncate tabular-nums">{decision.authorization_id}</dd>
        {decision.resolved_by && (
          <>
            <dt className="font-medium text-ink-soft">Resolved by</dt>
            <dd>{decision.resolved_by}</dd>
          </>
        )}
        {decision.engine_version && (
          <>
            <dt className="font-medium text-ink-soft">Engine</dt>
            <dd className="truncate">{decision.engine_version}</dd>
          </>
        )}
      </dl>
    </div>
  )
}
