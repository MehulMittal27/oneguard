import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import type { Account } from '../../api/types'
import { BackChevronIcon, PlusIcon } from '../../components/icons/lucide'
import { limitsFromMandate } from '../../lib/spend'
import { useCustomer } from '../../state/CustomerContext'
import { usePolicy } from '../../state/PolicyContext'

/** `mobile_and_online` -> `Mobile and online`, for the card's own subtitle. */
function humanise(value: string) {
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** A card chip rather than an icon — ink while guarded, grey while not. */
function CardChip({ guarded }: { guarded: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`relative h-[34px] w-[52px] shrink-0 rounded-meter ${
        guarded ? 'bg-ink' : 'bg-card-icon-gray'
      }`}
    >
      <span className="absolute bottom-[7px] left-[7px] h-[3px] w-3.5 rounded-[2px] bg-ground" />
    </span>
  )
}

type Status = 'loading' | 'error' | 'ready'

/**
 * DESIGN.md #5/#11 — one account card per account the customer has (D-057:
 * `getAccounts` now returns every account, not just the primary one, and
 * this screen already mapped over the array, so multi-account "support" was
 * just un-filtering the fixture, not a new UI). No account-switcher/dropdown
 * chrome (`AccountPicker`, #12) — stacking every account as its own card is
 * simpler and consistent with how Home and this screen's own card list
 * already show everything at once rather than behind a picker.
 * Card detail itself lives at the App level (D-041) — it's also reachable
 * cross-tab from a decision's "Policy applied" link, not just from here.
 */
export function Accounts({
  onAddPolicy,
  onViewCard,
}: {
  onAddPolicy: (cardId: string) => void
  onViewCard: (cardId: string) => void
}) {
  const { signedInAs } = useCustomer()
  const { policiesByCard, status: policiesStatus, retry: retryPolicies } = usePolicy()
  const [accountsStatus, setAccountsStatus] = useState<Status>('loading')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [attempt, setAttempt] = useState(0)

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
  }, [signedInAs, attempt])

  if (!signedInAs) return null

  // A card row says "No policy" only once C3 has said so (see Home).
  const status: Status =
    accountsStatus === 'error' || policiesStatus === 'error'
      ? 'error'
      : accountsStatus === 'ready' && policiesStatus === 'ready'
        ? 'ready'
        : 'loading'

  return (
    <div className="flex flex-col gap-7 px-8 pt-9 pb-9 sm:pt-5">
      <div>
        <h1 className="font-display text-[30px] font-bold text-ink">Accounts</h1>
        <p className="text-[14px] leading-[1.4] text-ink-muted">
          Policies belong to a card, not to you as a whole. Each card is guarded on its own.
        </p>
      </div>

      {status === 'loading' && (
        <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
          <div className="h-40 animate-pulse rounded-card bg-surface-sunken" />
          <span className="sr-only">Loading accounts</span>
        </div>
      )}

      {status === 'error' && (
        <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
          <p className="text-[15px] text-ink-soft">
            Couldn&apos;t load your accounts. Nothing shown here reflects what your agent can
            actually spend right now.
          </p>
          <button
            type="button"
            onClick={() => {
              if (accountsStatus === 'error') {
                setAccountsStatus('loading')
                setAttempt((n) => n + 1)
              }
              if (policiesStatus === 'error') retryPolicies()
            }}
            className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
          >
            Try again
          </button>
        </div>
      )}

      {status === 'ready' &&
        accounts.map((account) => (
          <div key={account.account_id} className="rounded-card bg-surface p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-display text-[19px] font-bold text-ink">
                  {humanise(account.account_purpose)}
                </p>
                {/* "N of M cards guarded" is the fact this screen exists to
                    tell: a policy protects one card, so an account is only
                    partly covered until every card on it has one. */}
                <p className="mt-0.5 text-[13px] text-ink-muted">
                  {humanise(account.account_type)} · {account.account_id} ·{' '}
                  {account.cards.filter((c) => policiesByCard[c.card_id]?.status === 'active').length}{' '}
                  of {account.cards.length} {account.cards.length === 1 ? 'card' : 'cards'} guarded
                </p>
              </div>
              <span className="shrink-0 rounded-pill bg-approved-tint px-2.5 py-1 text-[12px] font-semibold text-approved">
                Active
              </span>
            </div>

            <div className="mt-4 flex flex-col gap-2">
              <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
                Cards on this account
              </p>
              {account.cards.map((card) => {
                // A revoked mandate still exists in state (D-044) — route
                // to Card detail either way so its revoked/checks history
                // is reachable, not straight to "add a policy".
                const mandate = policiesByCard[card.card_id]
                const isActive = mandate?.status === 'active'
                const isRevoked = mandate?.status === 'revoked'
                const limitCount = mandate
                  ? [limitsFromMandate(mandate).perOrder, limitsFromMandate(mandate).period].filter(
                      Boolean,
                    ).length
                  : 0
                return (
                  <button
                    key={card.card_id}
                    type="button"
                    onClick={() => (isActive ? onViewCard(card.card_id) : onAddPolicy(card.card_id))}
                    className={`flex min-h-[72px] items-center gap-3 rounded-[18px] p-3 text-left ${
                      isActive
                        ? 'border-2 border-ink'
                        : 'border-2 border-dashed border-border-dashed'
                    }`}
                  >
                    <CardChip guarded={isActive} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[15px] font-semibold text-balance text-ink">
                        {humanise(card.card_purpose)} · {card.card_id}
                      </span>
                      <span className="block text-[13px] leading-[1.35] text-ink-muted">
                        {isActive
                          ? `${humanise(card.card_type)} · ${limitCount} ${limitCount === 1 ? 'limit' : 'limits'}`
                          : isRevoked
                            ? 'Policy revoked. The agent can’t spend here.'
                            : 'The agent can’t spend here.'}
                      </span>
                    </span>
                    {isActive ? (
                      <span className="flex shrink-0 items-center gap-1">
                        <span className="rounded-pill bg-approved-tint px-2.5 py-1 text-[12px] font-semibold text-approved">
                          Policy active
                        </span>
                        {/* A real drill-in to Card detail — the rotated back
                            chevron reads correctly as "view more" here. */}
                        <span className="rotate-180 text-ink-muted">
                          <BackChevronIcon size={18} strokeWidth={2} />
                        </span>
                      </span>
                    ) : (
                      // Neither a no-policy nor a revoked card is a detail view
                      // to browse — both need a new policy next, so both say
                      // that instead of a chevron (D-051, D-052). One row, one
                      // pill, one way forward: a revoked card used to show its
                      // status and nothing to do about it, so writing a new
                      // policy meant going through Card detail first.
                      <span className="flex shrink-0 items-center gap-1.5">
                        <span
                          className={`rounded-pill px-2.5 py-1 text-[12px] font-semibold ${
                            isRevoked
                              ? // Gray, not stopped-red (D-052) — a revoked policy
                                // isn't a declined purchase, it's just not active.
                                'bg-surface-expired text-ink-muted'
                              : 'bg-asked-tint text-asked'
                          }`}
                        >
                          {isRevoked ? 'Policy revoked' : 'No policy'}
                        </span>
                        <span className="flex items-center gap-1 text-[13px] font-semibold text-ink">
                          <PlusIcon size={14} strokeWidth={2.4} />
                          Add
                        </span>
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        ))}

      {/* A card with no policy is not "unprotected", it is shut. Without this the
          dashed rows read as a setup step someone forgot. */}
      {status === 'ready' && (
        <div className="flex flex-col gap-1 rounded-row bg-surface-active px-5 py-4">
          <p className="text-[14px] font-semibold text-ink">
            A card without a policy is closed to the agent
          </p>
          <p className="text-[13px] leading-[1.45] text-ink-soft">
            Nothing your agent proposes can be paid from it until you write and confirm a policy
            for that card.
          </p>
        </div>
      )}

      <p className="text-[13px] text-ink-muted">
        Bank limits on your accounts and cards are separate from your own spending policy — your
        policy is always the stricter of the two.
      </p>
    </div>
  )
}
