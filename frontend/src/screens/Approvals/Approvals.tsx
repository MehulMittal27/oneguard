import { useState } from 'react'
import { revokePolicy } from '../../api/policy'
import type { Decision, EvidenceItem, Mandate } from '../../api/types'
import { CountdownBar } from '../../components/CountdownBar'
import { DecisionMark } from '../../components/DecisionMark'
import { CheckIcon, CrossIcon, HelpCircleIcon, InfoIcon } from '../../components/icons/lucide'
import { OrderCapLeashMeter, PeriodLeashMeter } from '../../components/LeashMeter'
import { RevokeSheet } from '../../components/RevokeSheet'
import { SessionBanner } from '../../components/SessionBanner'
import { formatShortDate } from '../../lib/datetime'
import { messageWithoutCounterfactual, noteAddsToMessage } from '../../lib/decisionMessage'
import { formatChf } from '../../lib/money'
import { limitsFromMandate, spendFromMandate } from '../../lib/spend'
import { useDecisions } from '../../state/DecisionsContext'
import { usePolicy } from '../../state/PolicyContext'
import { DecisionDetail } from '../DecisionDetail/DecisionDetail'

// 'unknown' and 'not_applicable' are both real answers, never blank or "no"
// (data_dictionary.md) — same labels as DecisionDetail's banner.
const RETURNABLE_LABEL: Record<Decision['order_returnable'], string> = {
  true: 'Yes',
  false: 'No',
  unknown: 'Not stated by the shop',
  not_applicable: 'Not applicable',
}

const EVIDENCE_STYLE: Record<EvidenceItem['outcome'], { Icon: typeof CheckIcon; iconFg: string; border: string }> = {
  pass: { Icon: CheckIcon, iconFg: 'text-approved', border: 'border-hairline' },
  fail: { Icon: CrossIcon, iconFg: 'text-stopped', border: 'border-stopped-border' },
  uncertain: { Icon: HelpCircleIcon, iconFg: 'text-asked', border: 'border-asked-border' },
  // Neutral: context, not a verdict, and the fallback for unknown values.
  info: { Icon: InfoIcon, iconFg: 'text-ink-muted', border: 'border-hairline' },
}

function PendingCard({
  decision,
  decisions,
  mandate,
  onResolve,
}: {
  decision: Decision
  // Every decision on this card, so the "If you approve" preview reads the
  // same live totals CardDetail's own LeashMeter does — not a duplicate
  // calculation.
  decisions: Decision[]
  mandate: Mandate | undefined
  onResolve: (decision: 'approve' | 'decline') => Promise<void>
}) {
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState(false)
  const [revoking, setRevoking] = useState(false)
  const { revokePolicyForCard } = usePolicy()

  async function handle(answer: 'approve' | 'decline') {
    setResolving(true)
    setError(false)
    try {
      await onResolve(answer)
    } catch {
      setError(true)
    } finally {
      setResolving(false)
    }
  }

  // A revoked mandate has no live limit to preview against.
  const { perOrder, period } =
    mandate && mandate.status === 'active' ? limitsFromMandate(mandate) : { perOrder: null, period: null }
  // Ledger-first: `usage` when the engine sent it, the client sum only in mock mode.
  const spend = period
    ? spendFromMandate(mandate, decisions, decision.card_id, period.days)
    : null
  const cardDecisions = decisions
    .filter((d) => d.card_id === decision.card_id)
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))

  return (
    <div className="rounded-card border-2 border-asked-border bg-surface p-5">
      {/* Why the engine is being careful, above the purchase it is being careful
          about — the same banner DecisionDetail shows. */}
      {decision.session && decision.session.trust !== 'normal' && (
        <div className="mb-3">
          <SessionBanner session={decision.session} />
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <span className="rounded-pill bg-asked-tint px-3 py-1 text-[13px] font-medium text-asked">
          Waiting for you
        </span>
        <span className="text-[15px] font-semibold text-ink tabular-nums">
          {formatChf(decision.billing_amount_chf)}
        </span>
      </div>

      {/* deadline_at is always set for a pending_human decision — see
          src/api/decisions.ts. */}
      {decision.deadline_at && (
        <div className="mt-3">
          <CountdownBar deadlineAt={decision.deadline_at} />
        </div>
      )}

      <p className="mt-3 text-[15px] font-semibold text-ink">{decision.merchant.name}</p>

      <div className="mt-3 flex gap-6 border-t border-hairline pt-3 text-[13px]">
        <div>
          <p className="text-ink-muted">Returnable</p>
          <p className="mt-0.5 font-medium text-ink">
            {RETURNABLE_LABEL[decision.order_returnable]}
          </p>
        </div>
        {decision.delivery_by && (
          <div>
            <p className="text-ink-muted">Delivery by</p>
            <p className="mt-0.5 font-medium text-ink">{formatShortDate(decision.delivery_by)}</p>
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-col gap-2">
        {decision.items.map((item, index) => (
          <div
            key={index}
            className={`rounded-row px-4 py-3 ${
              decision.injection_flag
                ? 'border-[1.5px] border-asked-border bg-asked-tint'
                : 'border border-hairline bg-surface'
            }`}
          >
            {/* item_name/item_details are untrusted merchant text — plain text nodes only. */}
            <p className="text-[14px] font-medium text-ink">
              {item.quantity > 1 ? `${item.quantity}× ` : ''}
              {item.item_name}
            </p>
            <p className="mt-0.5 text-[12px] text-ink-muted">
              <span className="font-medium text-ink-soft">From the shop: </span>
              {item.item_details}
            </p>
          </div>
        ))}
      </div>

      {/*
        Both, not one or the other: `message` is the engine's reason for pausing
        and `uncertainty.note` is what specifically it can't settle. The note used
        to replace the message, which left the engine's own sentence unrendered on
        this screen (CLAUDE.md rule 10). The counterfactual is one line under the
        message, as in DecisionDetail: what would have let this through is the
        thing the customer weighs before answering. Said once: a message that
        still ends with the same suggestion drops its trailing copy. The note
        shows only when it says something the message does not (it is usually
        the uncertain evidence row's detail, listed just below anyway).
      */}
      <div className="mt-3 rounded-row border border-asked-border bg-asked-tint px-4 py-3">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-asked-ink uppercase">
          Why your rules are unsure
        </p>
        <p className="mt-1 text-[13px] font-medium text-asked-ink">
          {messageWithoutCounterfactual(decision.message, decision.counterfactual)}
        </p>
        {decision.counterfactual && (
          <p className="mt-1 text-[13px] text-asked-ink">{decision.counterfactual}</p>
        )}
        {decision.uncertainty && noteAddsToMessage(decision.message, decision.uncertainty.note) && (
          <p className="mt-2 text-[13px] text-asked-ink">{decision.uncertainty.note}</p>
        )}
      </div>

      {decision.evidence.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            What your rules checked
          </p>
          {decision.evidence.map((item, index) => {
            const style = EVIDENCE_STYLE[item.outcome] ?? EVIDENCE_STYLE.info
            return (
              <div
                key={index}
                className={`flex items-start gap-3 rounded-row border px-4 py-3 ${style.border}`}
              >
                <span className={`shrink-0 ${style.iconFg}`}>
                  <style.Icon size={16} strokeWidth={2.6} />
                </span>
                <div>
                  <p className="text-[14px] font-medium text-ink">{item.rule}</p>
                  <p className="mt-0.5 text-[12px] text-ink-muted">{item.detail}</p>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {(period || perOrder) && (
        <div className="mt-3 rounded-row border border-hairline bg-surface-sunken p-4">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            If you approve
          </p>
          <div className="mt-2">
            {period ? (
              <PeriodLeashMeter
                limitChf={period.limitChf}
                spentChf={spend?.spentChf ?? 0}
                pendingChf={spend?.pendingChf ?? 0}
                days={period.days}
              />
            ) : perOrder ? (
              <OrderCapLeashMeter capChf={perOrder} decisions={cardDecisions} />
            ) : null}
          </div>
        </div>
      )}

      {/*
        A step-up on a restriction no data can check ("an official ticket
        seller") is the one case where approving can also be remembered: the
        engine stores the answer against this shop and item and stops asking
        (engine/policy.py `is_unverifiable`). The button says so in full, so the
        customer is never agreeing to a standing rule by pressing a button that
        only said "Approve". It stacks rather than sharing the two-column row —
        the sentence does not fit half a 390px screen.
      */}
      {decision.confirmable ? (
        <div className="mt-4 flex flex-col gap-3">
          <button
            type="button"
            disabled={resolving}
            onClick={() => handle('approve')}
            className="min-h-14 rounded-row bg-approved px-4 py-3 text-[15px] leading-[1.35] font-semibold text-on-ink disabled:opacity-60"
          >
            {/* Merchant name is untrusted shop text — a plain text node here too. */}
            Approve, and treat {decision.merchant.name} as {decision.confirmable.phrase} from now
            on
          </button>
          <p className="text-[12px] text-ink-muted">
            Applies to this shop and the items in this order. Everything else still asks you.
          </p>
          <button
            type="button"
            disabled={resolving}
            onClick={() => handle('decline')}
            className="h-14 rounded-row border-2 border-destructive-border text-[16px] font-semibold text-destructive disabled:opacity-60"
          >
            Reject
          </button>
        </div>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <button
            type="button"
            disabled={resolving}
            onClick={() => handle('approve')}
            className="h-14 rounded-row bg-approved text-[16px] font-semibold text-on-ink disabled:opacity-60"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={resolving}
            onClick={() => handle('decline')}
            className="h-14 rounded-row border-2 border-destructive-border text-[16px] font-semibold text-destructive disabled:opacity-60"
          >
            Reject
          </button>
        </div>
      )}

      {error && (
        <p className="mt-3 text-[13px] text-destructive">
          Couldn&apos;t send your answer. Nothing was approved — try again.
        </p>
      )}

      <p className="mt-3 text-[12px] text-ink-muted">
        If the timer runs out, this request expires — nothing is approved or charged
        automatically.
      </p>

      {/* A shortcut to the same revoke as Card detail, for when this request
          looks like someone else is driving. */}
      {mandate?.status === 'active' && (
        <button
          type="button"
          onClick={() => setRevoking(true)}
          className="mt-2 min-h-11 text-[13px] font-semibold text-destructive"
        >
          Revoke policy
        </button>
      )}

      {revoking && (
        <RevokeSheet
          cardId={decision.card_id}
          onClose={() => setRevoking(false)}
          onConfirm={async () => {
            await revokePolicy(decision.card_id)
            revokePolicyForCard(decision.card_id)
            setRevoking(false)
          }}
        />
      )}
    </div>
  )
}

export function Approvals({
  onViewPolicy,
  onGoHome,
}: {
  onViewPolicy: (cardId: string) => void
  onGoHome: () => void
}) {
  const { decisions, pending, status, resolve, retry } = useDecisions()
  const { policiesByCard } = usePolicy()
  // Still under the Uncertain category (D-041) — this is a status section
  // within Approvals, not a 4th filter category like Activity's tiles.
  const expired = decisions.filter(
    (d) => d.decision === 'uncertain' && d.uncertain_outcome === 'expired',
  )
  const [viewingId, setViewingId] = useState<string | null>(null)
  // One pending decision fills the screen at a time (the 120s window it's
  // asking about deserves full focus, not a scroll past others) — clamped
  // rather than reset via an effect, so resolving the focused item just
  // reveals whichever pending item now sits at the same index.
  const [focusedIndex, setFocusedIndex] = useState(0)
  const safeIndex = pending.length === 0 ? 0 : Math.min(focusedIndex, pending.length - 1)
  const focused = pending[safeIndex]
  const others = pending.filter((_, i) => i !== safeIndex)

  if (viewingId) {
    return (
      <DecisionDetail
        authorizationId={viewingId}
        backLabel="Approvals"
        onBack={() => setViewingId(null)}
        onSelectRelated={setViewingId}
        onViewPolicy={onViewPolicy}
        onGoToApprovals={() => setViewingId(null)}
        onGoHome={onGoHome}
      />
    )
  }

  return (
    <div className="flex flex-col gap-7 px-8 pt-9 pb-9 sm:pt-5">
      <div>
        <h1 className="font-display text-[30px] font-bold text-ink">Approvals</h1>
        <p className="text-[15px] text-ink-muted">Purchases waiting for your answer.</p>
      </div>

      {status === 'loading' && (
        <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
          {[0, 1].map((i) => (
            <div key={i} className="h-40 animate-pulse rounded-card bg-surface-sunken" />
          ))}
          <span className="sr-only">Loading approvals</span>
        </div>
      )}

      {status === 'error' && (
        <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
          <p className="text-[15px] text-ink-soft">
            Couldn&apos;t load your approvals. Nothing shown here was approved while we were
            offline.
          </p>
          <button
            type="button"
            onClick={retry}
            className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
          >
            Try again
          </button>
        </div>
      )}

      {status === 'ready' && pending.length === 0 && (
        <p className="text-[15px] text-ink-muted">Nothing is waiting for you right now.</p>
      )}

      {status === 'ready' && focused && (
        <div className="flex flex-col gap-3">
          {pending.length > 1 && (
            <div
              className="flex justify-center gap-1.5"
              role="tablist"
              aria-label="Choose which pending purchase to review"
            >
              {pending.map((decision, i) => (
                <button
                  key={decision.authorization_id}
                  type="button"
                  role="tab"
                  aria-selected={i === safeIndex}
                  aria-label={`Purchase ${i + 1} of ${pending.length}`}
                  onClick={() => setFocusedIndex(i)}
                  className={`h-1.5 rounded-pill transition-[width] ${
                    i === safeIndex ? 'w-5 bg-asked' : 'w-1.5 bg-hairline'
                  }`}
                />
              ))}
            </div>
          )}

          <PendingCard
            key={focused.authorization_id}
            decision={focused}
            decisions={decisions}
            mandate={policiesByCard[focused.card_id]}
            onResolve={(answer) => resolve(focused.authorization_id, answer)}
          />

          {others.length > 0 && (
            <button
              type="button"
              onClick={() => {
                const nextIndex = pending.findIndex(
                  (d) => d.authorization_id === others[0].authorization_id,
                )
                if (nextIndex >= 0) setFocusedIndex(nextIndex)
              }}
              className="flex min-h-11 items-center justify-between gap-3 rounded-row bg-surface-sunken px-4 py-3 text-left"
            >
              <span className="text-[12px] text-ink-muted">Also waiting</span>
              <span className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[13px] font-semibold text-ink">
                  {others[0].merchant.name} · {formatChf(others[0].billing_amount_chf)}
                </span>
                <span className="shrink-0 text-ink-muted">›</span>
              </span>
            </button>
          )}
        </div>
      )}

      {expired.length > 0 && (
        <div>
          <p className="mb-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            Expired · {expired.length}
          </p>
          <div className="flex flex-col gap-1">
            {expired.map((decision) => (
              <DecisionMark
                key={decision.authorization_id}
                decision={decision}
                onClick={() => setViewingId(decision.authorization_id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
