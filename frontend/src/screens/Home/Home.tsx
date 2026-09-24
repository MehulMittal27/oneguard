import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import type { Account, Decision } from '../../api/types'
import { AccountMenu } from '../../components/AccountMenu'
import { CountdownBar } from '../../components/CountdownBar'
import { DecisionMark } from '../../components/DecisionMark'
import { OverviewHero } from '../../components/OverviewHero'
import { AccountsIcon, BackChevronIcon, BellIcon } from '../../components/icons/lucide'
import { formatChf } from '../../lib/money'
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
}: {
  onOpenActivity: (filter: FilterId) => void
  onGoToApprovals: () => void
  onViewPolicy: (cardId: string) => void
}) {
  const { signedInAs, logout } = useCustomer()
  const { policiesByCard } = usePolicy()
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

  // Every card with an active policy, across every account this customer
  // has — not just their one "primary" card (2 of 4 live customers'
  // account already has a second card, D-050). A revoked or missing
  // policy isn't shown here at all; that's what Accounts/Card detail are for.
  const activePolicies = accounts.flatMap((account) =>
    account.cards.flatMap((card) => {
      const mandate = policiesByCard[card.card_id]
      if (mandate?.status !== 'active') return []
      return [{ cardId: card.card_id, accountId: account.account_id, mandate }]
    }),
  )

  const latest = [...decisions]
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
    .slice(0, 3)

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
            Testing
          </span>
          <AccountMenu customerName={signedInAs.name} onLogout={logout} />
        </div>
      </header>

      <div className="flex flex-col gap-7 px-8 pb-9">
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

        {status === 'ready' && (
          <OverviewHero decisions={decisions} onOpenActivity={onOpenActivity} />
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
                {pending.length === 1
                  ? '1 purchase needs your review'
                  : `${pending.length} purchases need your review`}
              </span>
            </span>

            <span className="flex items-start justify-between gap-3">
              <span className="min-w-0">
                {/* Merchant name is untrusted merchant text — plain text node only. */}
                <span className="block truncate text-[15px] font-semibold text-ink">
                  {mostUrgent.merchant.name}
                </span>
                <span className="block truncate text-[13px] text-ink-muted">
                  {mostUrgent.uncertainty?.note ?? mostUrgent.message}
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
          </button>
        )}

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
                    onClick={() => selectDecision(decision)}
                  />
                ))}
              </div>
            </div>
          </section>
        )}

        <section className="flex flex-col gap-3">
          <p className="font-display text-[20px] font-bold text-ink">Active policies</p>

          {accountsStatus === 'loading' && (
            <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
              <div className="h-16 animate-pulse rounded-row bg-surface-sunken" />
              <span className="sr-only">Loading policies</span>
            </div>
          )}

          {accountsStatus === 'error' && (
            <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
              <p className="text-[15px] text-ink-soft">Couldn&apos;t load your policies.</p>
              <button
                type="button"
                onClick={() => {
                  setAccountsStatus('loading')
                  setAccountsAttempt((n) => n + 1)
                }}
                className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
              >
                Try again
              </button>
            </div>
          )}

          {accountsStatus === 'ready' && activePolicies.length === 0 && (
            <p className="text-[15px] text-ink-muted">No active policies yet.</p>
          )}

          {accountsStatus === 'ready' && activePolicies.length > 0 && (
            <div className="scrollbar-none max-h-72 overflow-y-auto rounded-card border border-hairline bg-surface p-2">
              <div className="flex flex-col gap-1">
                {activePolicies.map(({ cardId, accountId, mandate }) => (
                  <button
                    key={cardId}
                    type="button"
                    onClick={() => onViewPolicy(cardId)}
                    className="flex min-h-16 w-full items-center gap-4 rounded-row px-4 py-3 text-left"
                  >
                    <span className="flex size-9.5 shrink-0 items-center justify-center rounded-full bg-approved-tint text-approved">
                      <AccountsIcon size={20} strokeWidth={1.8} />
                    </span>
                    <span className="min-w-0 flex-1">
                      {/* The customer's own words, not merchant text — but
                          still just a display value, never re-parsed. */}
                      <span className="block truncate text-[15px] font-semibold text-ink">
                        {mandate.instruction || 'Spending policy'}
                      </span>
                      <span className="block truncate text-[13px] text-ink-muted">
                        Card {cardId} · Account {accountId}
                      </span>
                    </span>
                    <span className="rotate-180 shrink-0 text-ink-muted">
                      <BackChevronIcon size={18} strokeWidth={2} />
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>
    </>
  )
}
