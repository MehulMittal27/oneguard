import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import type { Account, Decision } from '../../api/types'
import { AccountMenu } from '../../components/AccountMenu'
import { CountdownBar } from '../../components/CountdownBar'
import { DecisionMark } from '../../components/DecisionMark'
import { PendingDevicesCard } from '../../components/PendingDevicesCard'
import { BackChevronIcon, BellIcon, PlusIcon } from '../../components/icons/lucide'
import { formatChf } from '../../lib/money'
import { splitByRun } from '../../lib/runs'
import { useCustomer } from '../../state/CustomerContext'
import { useDecisions } from '../../state/DecisionsContext'
import { usePolicy } from '../../state/PolicyContext'
import type { FilterId } from '../Activity/Activity'
import { DecisionDetail } from '../DecisionDetail/DecisionDetail'

type PolicyStatus = 'loading' | 'error' | 'ready'

/**
 * Header (DESIGN.md #2/#10, D-021), `OverviewHero`, "Needs your review" (shown
 * only while something is pending), "Latest by your control", then "Active
 * policies" — a read-only summary, not a management surface (D-053):
 * tapping a row opens Card detail, where Add/Revoke actually live.
 */
export function Home({
  onOpenActivity,
  onGoToApprovals,
  onViewPolicy,
  onAddPolicy,
}: {
  onOpenActivity: (filter: FilterId) => void
  onGoToApprovals: () => void
  onViewPolicy: (cardId: string) => void
  onAddPolicy: (cardId: string) => void
}) {
  const { signedInAs, logout } = useCustomer()
  const { policiesByCard, status: policiesStatus, retry: retryPolicies } = usePolicy()
  const { decisions, pending, status, retry } = useDecisions()
  const [viewingId, setViewingId] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountsStatus, setAccountsStatus] = useState<PolicyStatus>('loading')
  const [accountsAttempt, setAccountsAttempt] = useState(0)

  useEffect(() => {
    if (!signedInAs) return
    let cancelled = false
    getAccounts(signedInAs.customer_id)
      .then((result) => {
        if (cancelled) return
        setAccounts(result)
        setAccountsStatus('ready')
      })
      .catch(() => {
        if (cancelled) return
        setAccountsStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [signedInAs, accountsAttempt])

  if (!signedInAs) return null

  // The cards come from C10, their policies from C3. Until both are in, no card
  // may read "No policy yet": that is only true once C3 has said `null`.
  const policiesSection: PolicyStatus =
    accountsStatus === 'error' || policiesStatus === 'error'
      ? 'error'
      : accountsStatus === 'ready' && policiesStatus === 'ready'
        ? 'ready'
        : 'loading'

  const cardsByAccount = accounts.map((account) => ({
    account,
    cards: account.cards.map((card) => ({ card, mandate: policiesByCard[card.card_id] })),
  }))

  // Each card's newest run, so replayed older data is not shown twice.
  const { current } = splitByRun(decisions)
  const byNewest = (a: Decision, b: Decision) => b.occurred_at.localeCompare(a.occurred_at)
  const latest = [...current].sort(byNewest).slice(0, 3)

  // Whichever pending item runs out first, in case more than one is ever
  // waiting at once — our live customers only ever have one, but nothing
  // here assumes that.
  const mostUrgent = [...pending].sort((a, b) =>
    (a.deadline_at ?? '').localeCompare(b.deadline_at ?? ''),
  )[0]

  // A still-pending uncertain purchase is actionable, not just viewable —
  // route straight to where it can actually be answered (matches Activity,
  // Approvals, Card detail).
  function selectDecision(decision: Decision) {
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
        backLabel="Home"
        onBack={() => setViewingId(null)}
        onSelectRelated={setViewingId}
        onViewPolicy={onViewPolicy}
        onGoToApprovals={onGoToApprovals}
        onGoHome={() => setViewingId(null)}
      />
    )
  }

  return (
    <>
      <header className="flex items-center justify-between px-8 pt-9 pb-7 sm:pt-5">
        <div className="min-w-0">
          <p className="truncate font-display text-[30px] font-bold text-ink">
            {signedInAs.name}
          </p>
          <p className="truncate text-[13px] text-ink-muted">{signedInAs.home_region}</p>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <span className="flex items-center gap-1.5 rounded-pill bg-approved-tint px-3 py-1 text-[13px] font-medium text-approved">
            <span className="size-1.5 shrink-0 rounded-full bg-approved" aria-hidden="true" />
            Sandbox
          </span>
          <AccountMenu customerName={signedInAs.name} onLogout={logout} />
        </div>
      </header>

      <div className="flex flex-col gap-7 px-8 pb-9">
        {(signedInAs.card_id || (accountsStatus === 'ready' && accounts[0]?.cards[0]?.card_id)) && (
          <button
            type="button"
            onClick={() => onAddPolicy(signedInAs.card_id ?? accounts[0].cards[0].card_id)}
            className="flex min-h-11 items-center gap-4 rounded-card border-2 border-dashed border-border-dashed bg-surface p-5 text-left"
          >
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-ink text-on-ink">
              <PlusIcon size={21} strokeWidth={2.4} />
            </span>
            <span>
              <span className="block text-[16px] font-semibold text-ink">Add a spending policy</span>
              <span className="block text-[13px] text-ink-muted">
                Set limits, allowed shops, and what needs your OK.
              </span>
            </span>
          </button>
        )}
        {status === 'loading' && (
          <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
            <div className="h-[220px] animate-pulse rounded-hero bg-surface-sunken" />
            <div className="h-24 animate-pulse rounded-card bg-surface-sunken" />
            <span className="sr-only">Loading your overview</span>
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
            <p className="text-[15px] text-ink-soft">
              Couldn&apos;t load your activity. Nothing shown here was approved or stopped while
              we were offline.
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

        {status === 'ready' && mostUrgent && (
          <button
            type="button"
            onClick={onGoToApprovals}
            className="flex min-h-11 flex-col gap-3 rounded-card border-2 border-asked-border bg-surface p-5 text-left"
          >
            <span className="flex items-center gap-2 text-asked">
              <BellIcon size={18} strokeWidth={2.2} />
              <span className="text-[15px] font-semibold">
                Needs your OK
              </span>
            </span>

            <span className="flex items-start justify-between gap-3">
              <span className="min-w-0">
                {/* Merchant name is untrusted merchant text — plain text node only. */}
                <span className="block truncate text-[15px] font-semibold text-ink">
                  {mostUrgent.merchant.name}
                </span>
                {/* The engine's own reason, not the uncertainty note that used
                    to stand in for it (CLAUDE.md rule 10). */}
                <span className="block truncate text-[13px] text-ink-muted">
                  {mostUrgent.message}
                </span>
              </span>
              <span className="shrink-0 text-[15px] font-semibold text-ink tabular-nums">
                {formatChf(mostUrgent.billing_amount_chf)}
              </span>
            </span>

            {mostUrgent.deadline_at && <CountdownBar deadlineAt={mostUrgent.deadline_at} />}

            {pending.length > 1 && (
              <span className="text-[12px] text-ink-muted">
                +{pending.length - 1} more waiting
              </span>
            )}
            <span className="self-center text-[13px] font-semibold text-ink">Review and decide</span>
          </button>
        )}

        {/* Approving happens in Card detail, which a card has once it has (or had) a policy. */}
        <PendingDevicesCard
          cardIds={accounts.flatMap((account) =>
            account.cards.filter((card) => policiesByCard[card.card_id]).map((card) => card.card_id),
          )}
          onOpenCard={onViewPolicy}
        />

        {status === 'ready' && latest.length > 0 && (
          <section className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <p className="font-display text-[20px] font-bold text-ink">Latest by your control</p>
              <button
                type="button"
                onClick={() => onOpenActivity('all')}
                className="min-h-11 text-[13px] font-semibold text-ink-muted underline underline-offset-2"
              >
                See all
              </button>
            </div>
            <div className="rounded-card border border-hairline bg-surface p-2">
              <div className="flex flex-col gap-1">
                {latest.map((decision) => (
                  <DecisionMark
                    key={decision.authorization_id}
                    decision={decision}
                    compact
                    onClick={() => selectDecision(decision)}
                  />
                ))}
              </div>
            </div>
          </section>
        )}

        <section className="flex flex-col gap-3">
          <p className="font-display text-[20px] font-bold text-ink">Policies by card</p>

          {policiesSection === 'loading' && (
            <div className="h-24 animate-pulse rounded-card bg-surface-sunken" aria-label="Loading policies" />
          )}

          {policiesSection === 'error' && (
            <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
              <p className="text-[15px] text-ink-soft">Couldn&apos;t load your policies.</p>
              <button
                type="button"
                onClick={() => {
                  if (accountsStatus === 'error') {
                    setAccountsStatus('loading')
                    setAccountsAttempt((n) => n + 1)
                  }
                  if (policiesStatus === 'error') retryPolicies()
                }}
                className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
              >
                Try again
              </button>
            </div>
          )}

          {policiesSection === 'ready' && cardsByAccount.length > 0 && (
            <div className="flex flex-col gap-3">
              {cardsByAccount.flatMap(({ account, cards }) =>
                cards.map(({ card, mandate }) => {
                  const active = mandate?.status === 'active'
                  const revoked = mandate?.status === 'revoked'
                  const policyTitle = active
                    ? mandate.checks.find((check) => check.kind === 'item')?.text ?? `Card ${card.card_id}`
                    : card.card_purpose.replace(/_/g, ' ')
                  return (
                    <div key={card.card_id} className="rounded-card border border-hairline bg-surface p-4">
                      <button
                        type="button"
                        onClick={() => (active || revoked ? onViewPolicy(card.card_id) : onAddPolicy(card.card_id))}
                        className="flex w-full items-start justify-between gap-3 text-left"
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-[16px] font-semibold text-ink">
                            {policyTitle}
                          </span>
                          <span className="mt-0.5 block text-[13px] text-ink-muted">
                            {card.card_type.replace(/_/g, ' ')} · Card {card.card_id}
                          </span>
                          <span className="block text-[12px] text-ink-muted">
                            {card.card_purpose.replace(/_/g, ' ')} card · {account.account_purpose.replace(/_/g, ' ')} account · {account.account_id}
                          </span>
                        </span>
                        <span className={`shrink-0 rounded-[8px] px-2.5 py-1 text-[12px] font-semibold ${active ? 'bg-approved-tint text-approved' : revoked ? 'bg-surface-expired text-ink-muted' : 'bg-asked-tint text-asked'}`}>
                          {active ? 'Active' : revoked ? 'Revoked' : 'No policy'}
                        </span>
                        <span className="shrink-0 rotate-180 text-ink-muted" aria-hidden="true">
                          <BackChevronIcon size={18} strokeWidth={2} />
                        </span>
                      </button>
                      {active ? (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {mandate.checks.map((check) => (
                            <span key={check.id} className="rounded-pill bg-surface-sunken px-3 py-1.5 text-[12px] text-ink-soft">
                              {check.text}
                            </span>
                          ))}
                          <span className="rounded-pill bg-surface-sunken px-3 py-1.5 text-[12px] text-ink-soft">
                            {mandate.uncertainty_policy === 'decline' ? 'Block when unsure' : mandate.uncertainty_policy === 'approve' ? 'Approve when unsure' : 'Ask when unsure'}
                          </span>
                        </div>
                      ) : null}
                    </div>
                  )
                }),
              )}
            </div>
          )}

          {policiesSection === 'ready' && cardsByAccount.length === 0 && (
            <p className="text-[15px] text-ink-muted">No cards are available yet.</p>
          )}
        </section>
      </div>
    </>
  )
}
