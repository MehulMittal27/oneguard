import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import { revokePolicy } from '../../api/policy'
import type { Account } from '../../api/types'
import { DecisionMark } from '../../components/DecisionMark'
import { BackChevronIcon, CheckIcon, HelpCircleIcon } from '../../components/icons/lucide'
import { NetworkState } from '../../components/NetworkState'
import { RevokeSheet } from '../../components/RevokeSheet'
import { usePolicy } from '../../state/PolicyContext'
import { useDecisions } from '../../state/DecisionsContext'
import { useCustomer } from '../../state/CustomerContext'
import { DecisionDetail } from '../DecisionDetail/DecisionDetail'
import type { FilterId } from '../Activity/Activity'

function humanise(value: string): string {
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** DESIGN.md #6, "Card activity" — reached from Accounts, or cross-tab from a decision's "Policy" link. */
export function CardDetail({
  cardId,
  onBack,
  onGoToApprovals,
  onAddPolicy,
  onOpenActivity,
  onGoHome,
}: {
  cardId: string
  onBack: () => void
  onGoToApprovals: () => void
  onAddPolicy: (cardId: string) => void
  onOpenActivity: (filter: FilterId) => void
  onGoHome: () => void
}) {
  const {
    policiesByCard,
    status: policiesStatus,
    retry: retryPolicies,
    revokePolicyForCard,
  } = usePolicy()
  const { decisions, status: decisionsStatus, retry: retryDecisions } = useDecisions()
  const { signedInAs } = useCustomer()
  const [revoking, setRevoking] = useState(false)
  const [viewingId, setViewingId] = useState<string | null>(null)
  const [account, setAccount] = useState<Account | null>(null)

  useEffect(() => {
    if (!signedInAs) return
    let cancelled = false
    getAccounts(signedInAs.customer_id)
      .then((accounts) => {
        if (cancelled) return
        setAccount(accounts.find((candidate) => candidate.cards.some((card) => card.card_id === cardId)) ?? null)
      })
      .catch(() => {
        if (!cancelled) setAccount(null)
      })
    return () => {
      cancelled = true
    }
  }, [cardId, signedInAs])

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
        {/* A failed C3 is not "no policy": say it failed, never imply the card is unguarded. */}
        {policiesStatus === 'loading' ? (
          <div aria-live="polite" aria-busy="true">
            <div className="h-40 animate-pulse rounded-card bg-surface-sunken" />
            <span className="sr-only">Loading policy</span>
          </div>
        ) : policiesStatus === 'error' ? (
          <NetworkState kind="error" label="this card's policy" onRetry={retryPolicies} />
        ) : (
          <p className="text-[15px] text-ink-muted">No policy found for card {cardId}.</p>
        )}
      </div>
    )
  }

  // A revoked mandate stays in state instead of being deleted (D-044), so
  // this screen can still show what it used to say.
  const isRevoked = mandate.status === 'revoked'
  const cardInfo = account?.cards.find((card) => card.card_id === cardId)

  const cardDecisions = decisions
    .filter((d) => d.card_id === cardId)
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
  // Absent until the backend sends it (see types.ts) — an empty list, never a guess.
  const confirmations = mandate.usage?.confirmations ?? []

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
        <h1 className="font-display text-[30px] font-bold text-ink">
          {cardInfo ? humanise(cardInfo.card_purpose) : `Card ${cardId}`}
        </h1>
        <p className="text-[13px] text-ink-muted">
          {cardId}{account ? ` · ${humanise(account.account_purpose)} account` : ''}
        </p>
      </div>

      <div className="rounded-card bg-surface p-5">
        <div className="flex items-start justify-between gap-3">
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
          <div className="flex items-start gap-2">
            <span className="text-approved"><CheckIcon size={18} strokeWidth={2.4} /></span>
            <p className="text-[15px] text-ink-soft">
              {mandate.uncertainty_policy === 'decline'
                ? 'When unsure: stops the purchase.'
                : mandate.uncertainty_policy === 'approve'
                  ? 'When unsure: approves the purchase.'
                  : 'When unsure: asks you, never guesses.'}
            </p>
          </div>
        </div>

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
              You can remove this policy at any time. Revoking declines purchases received after
              the platform confirms the change. Anything already waiting for your answer is
              unaffected.
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

      {/*
        Answers the customer gave once that the engine now remembers, so it stops
        asking (engine/policy.py: only a restriction no data can check can be
        passed this way). Read-only on purpose — this screen shows what is
        remembered, and a policy is tightened or revoked, never edited here.
        Hidden when empty: a heading over nothing would imply the engine is
        remembering things it is not.
      */}
      {confirmations.length > 0 && (
        <div>
          <p className="mb-3 font-display text-[20px] font-bold text-ink">
            Things you&apos;ve confirmed
          </p>
          <ul className="flex flex-col gap-2">
            {confirmations.map((c, index) => (
              <li
                key={`${c.rule_text}-${c.merchant_name}-${c.item_name}-${index}`}
                className="rounded-row border border-hairline bg-surface px-4 py-3"
              >
                <p className="text-[15px] font-medium text-ink">{c.rule_text}</p>
                {/* Shop and item names are untrusted text — plain text nodes. */}
                <p className="mt-0.5 text-[13px] text-ink-muted">
                  {c.merchant_name} · {c.item_name}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between">
          <p className="font-display text-[20px] font-bold text-ink">Card activity</p>
          <button type="button" onClick={() => onOpenActivity('all')} className="min-h-11 text-[13px] font-semibold text-ink-muted underline underline-offset-2">All activity</button>
        </div>
        {decisionsStatus === 'loading' ? (
          <NetworkState kind="loading" label="card activity" />
        ) : decisionsStatus === 'error' ? (
          <NetworkState kind="error" label="card activity" onRetry={retryDecisions} />
        ) : cardDecisions.length === 0 ? (
          <NetworkState kind="empty" label="card activity">
            Nothing on this card yet.
          </NetworkState>
        ) : (
          <div className="flex flex-col gap-1">
            {cardDecisions.map((decision) => (
              <DecisionMark
                key={decision.authorization_id}
                decision={decision}
                timestamp="day-time"
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
