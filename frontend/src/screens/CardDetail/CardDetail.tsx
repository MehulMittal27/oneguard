import { useState } from 'react'
import { revokePolicy } from '../../api/policy'
import { DecisionMark } from '../../components/DecisionMark'
import { BackChevronIcon, CheckIcon, HelpCircleIcon } from '../../components/icons/lucide'
import { OrderCapLeashMeter, PeriodLeashMeter } from '../../components/LeashMeter'
import { RevokeSheet } from '../../components/RevokeSheet'
import { formatShortDate } from '../../lib/datetime'
import { limitsFromMandate, spendFromMandate } from '../../lib/spend'
import { usePolicy } from '../../state/PolicyContext'
import { useDecisions } from '../../state/DecisionsContext'
import { DecisionDetail } from '../DecisionDetail/DecisionDetail'

/** DESIGN.md #6, "Card activity" — reached from Accounts, or cross-tab from a decision's "Policy" link. */
export function CardDetail({
  cardId,
  onBack,
  onGoToApprovals,
  onAddPolicy,
  onGoHome,
}: {
  cardId: string
  onBack: () => void
  onGoToApprovals: () => void
  onAddPolicy: (cardId: string) => void
  onGoHome: () => void
}) {
  const { policiesByCard, revokePolicyForCard } = usePolicy()
  const { decisions } = useDecisions()
  const [revoking, setRevoking] = useState(false)
  const [viewingId, setViewingId] = useState<string | null>(null)

  const mandate = policiesByCard[cardId]
  // Normally unreachable — every entry point only links here for a card
  // that has (or had) a policy — but a blank screen is still a bug if it
  // ever happens (ROADMAP.md slice 9: no screen fails silently).
  if (!mandate) {
    return (
      <div className="flex flex-col gap-7 px-8 pt-4 pb-9 sm:pt-5">
        <button
          type="button"
          onClick={onBack}
          className="flex min-h-11 items-center gap-1 text-[15px] font-semibold text-ink-muted"
        >
          <BackChevronIcon size={20} strokeWidth={2} />
          Accounts
        </button>
        <p className="text-[15px] text-ink-muted">
          Nothing found for card {cardId} in this session.
        </p>
      </div>
    )
  }

  // A revoked mandate stays in state instead of being deleted (D-044), so
  // this screen can still show what it used to say.
  const isRevoked = mandate.status === 'revoked'

  const cardDecisions = decisions
    .filter((d) => d.card_id === cardId)
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
  const { perOrder, period } = limitsFromMandate(mandate)
  // Ledger-first: `usage` when the engine sent it, the client sum only in mock mode.
  const spend = period ? spendFromMandate(mandate, decisions, cardId, period.days) : null

  // A still-pending uncertain purchase is actionable, not just viewable —
  // route straight to where it can actually be answered.
  function selectDecision(decision: (typeof cardDecisions)[number]) {
    if (decision.status === 'pending_human') {
      onGoToApprovals()
    } else {
      setViewingId(decision.authorization_id)
    }
  }

  if (viewingId) {
    return (
      <DecisionDetail
        authorizationId={viewingId}
        backLabel={`Card ${cardId}`}
        onBack={() => setViewingId(null)}
        onSelectRelated={setViewingId}
        onViewPolicy={() => setViewingId(null)}
        onGoToApprovals={onGoToApprovals}
        onGoHome={onGoHome}
      />
    )
  }

  return (
    <div className="flex flex-col gap-7 px-8 pt-4 pb-9 sm:pt-5">
      <button
        type="button"
        onClick={onBack}
        className="flex min-h-11 items-center gap-1 text-[15px] font-semibold text-ink-muted"
      >
        <BackChevronIcon size={20} strokeWidth={2} />
        Accounts
      </button>

      <div>
        <h1 className="font-display text-[30px] font-bold text-ink">Card {cardId}</h1>
      </div>

      <div className="rounded-card bg-surface p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-display text-[17px] font-bold text-ink">Spending policy</p>
            <p className="mt-0.5 text-[13px] text-ink-muted">
              Created {formatShortDate(mandate.confirmed_at)}
            </p>
          </div>
          <span
            className={`shrink-0 rounded-pill px-3 py-1 text-[13px] font-medium ${
              isRevoked ? 'bg-stopped-tint text-stopped' : 'bg-approved-tint text-approved'
            }`}
          >
            {isRevoked ? 'Revoked' : 'Active'}
          </span>
        </div>

        {isRevoked && (
          <p className="mt-3 text-[13px] text-stopped">
            This policy was revoked. Your agent can&apos;t spend on this card until you add a new
            one. The checks below are what it used to say.
          </p>
        )}

        {mandate.instruction && (
          <div className="mt-4 rounded-row border border-hairline bg-surface-sunken p-4">
            <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
              Your words
            </p>
            <p className="mt-2 text-[15px] text-ink-soft">{mandate.instruction}</p>
          </div>
        )}

        <div className="mt-4 flex flex-col gap-2">
          <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
            What your rules do
          </p>
          {mandate.checks.map((check) => (
            <div key={check.id} className="flex items-start gap-2">
              <span className={check.source === 'exact' ? 'text-approved' : 'text-asked'}>
                {check.source === 'exact' ? (
                  <CheckIcon size={18} strokeWidth={2.4} />
                ) : (
                  <HelpCircleIcon size={18} strokeWidth={2.2} />
                )}
              </span>
              <p className="text-[15px] text-ink-soft">{check.text}</p>
            </div>
          ))}
        </div>

        {/* A revoked policy has no live limit to meter spend against. */}
        {!isRevoked && (
          <div className="mt-5">
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
        )}

        {isRevoked ? (
          <button
            type="button"
            onClick={() => onAddPolicy(cardId)}
            className="mt-4 h-11.5 w-full rounded-button bg-ink text-[15px] font-semibold text-on-ink"
          >
            Add policy
          </button>
        ) : (
          <>
            {/* The mockup's note says revoking "declines anything still waiting",
                which this system does not do: `../docs/rules.md` Q6 declines what
                arrives *after* a revoke, and Appendix A keeps revoke off anything
                in flight. */}
            <p className="mt-4 text-[13px] leading-[1.45] text-ink-muted">
              A policy can only be tightened or revoked — loosening it means writing a new one.
              After you revoke, anything your agent proposes next is declined; a purchase already
              waiting for your answer is unaffected until the platform confirms it.
            </p>
            <button
              type="button"
              onClick={() => setRevoking(true)}
              className="mt-4 h-11.5 w-full rounded-button border-2 border-destructive-border text-[15px] font-semibold text-destructive"
            >
              Revoke
            </button>
          </>
        )}
      </div>

      <div>
        <p className="mb-3 font-display text-[20px] font-bold text-ink">Card activity</p>
        {cardDecisions.length === 0 ? (
          <p className="text-[15px] text-ink-muted">Nothing on this card yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {cardDecisions.map((decision) => (
              <DecisionMark
                key={decision.authorization_id}
                decision={decision}
                onClick={() => selectDecision(decision)}
              />
            ))}
          </div>
        )}
      </div>

      {revoking && (
        <RevokeSheet
          cardId={cardId}
          onClose={() => setRevoking(false)}
          onConfirm={async () => {
            await revokePolicy(cardId)
            revokePolicyForCard(cardId)
            setRevoking(false)
            // Stays on this screen (was: onBack()) — it now shows the
            // "Revoked" state directly instead of bouncing to Accounts.
          }}
        />
      )}
    </div>
  )
}
