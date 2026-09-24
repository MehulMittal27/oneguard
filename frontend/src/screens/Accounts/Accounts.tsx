import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import type { Account } from '../../api/types'
import { AccountsIcon, BackChevronIcon, PlusIcon } from '../../components/icons/lucide'
import { useCustomer } from '../../state/CustomerContext'
import { usePolicy } from '../../state/PolicyContext'

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
  const { policiesByCard } = usePolicy()
  const [status, setStatus] = useState<Status>('loading')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!signedInAs) return
    let cancelled = false
    getAccounts(signedInAs.customer_id)
      .then((result) => {
        if (cancelled) return
        setAccounts(result)
        setStatus('ready')
      })
      .catch(() => {
        if (cancelled) return
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [signedInAs, attempt])

  if (!signedInAs) return null

  return (
    <div className="flex flex-col gap-7 px-8 pt-9 pb-9 sm:pt-5">
      <div>
        <h1 className="font-display text-[30px] font-bold text-ink">Accounts</h1>
        <p className="text-[15px] text-ink-muted">Your accounts and the cards on them.</p>
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
              setStatus('loading')
              setAttempt((n) => n + 1)
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
              <div>
                <p className="font-display text-[17px] font-bold text-ink capitalize">
                  {account.account_type} · {account.account_purpose.replace('_', ' ')}
                </p>
                <p className="text-[13px] text-ink-muted">Account {account.account_id}</p>
              </div>
              <span className="shrink-0 rounded-pill bg-approved-tint px-3 py-1 text-[13px] font-medium text-approved">
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
                return (
                  <button
                    key={card.card_id}
                    type="button"
                    onClick={() =>
                      mandate ? onViewCard(card.card_id) : onAddPolicy(card.card_id)
                    }
                    className={`flex min-h-16 items-center gap-4 rounded-row px-4 py-3 text-left ${
                      mandate ? 'border border-ink' : 'border-2 border-dashed border-border-dashed'
                    }`}
                  >
                    <span
                      className={`flex size-[38px] shrink-0 items-center justify-center rounded-full ${
                        isActive
                          ? 'bg-approved-tint text-approved'
                          : isRevoked
                            ? // Gray, not stopped-red (D-052) — a revoked
                              // policy isn't a declined purchase, it's just
                              // not active, same reasoning as the
                              // Uncertain-expired gray elsewhere.
                              'bg-surface-expired text-ink-muted'
                            : // DESIGN.md's card-icon-gray (#B7B0A0) fails
                              // contrast against a white card (2.16:1, below
                              // even the 3:1 icon minimum) — ink-muted
                              // instead (ROADMAP.md slice 9 accessibility pass).
                              'text-ink-muted'
                      }`}
                    >
                      <AccountsIcon size={20} strokeWidth={1.8} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-semibold text-ink capitalize">
                        Card {card.card_id} · {card.card_purpose}
                      </span>
                      {/* No caption for "no policy yet" (D-052) — the
                          trailing "Add policy" label already says it. */}
                      {isActive && (
                        <span className="block truncate text-[13px] text-ink-muted">
                          Policy active
                        </span>
                      )}
                      {/* Just "Policy revoked" (D-053) — the trailing
                          "Add policy" label already says what's next, no
                          need to say it twice. */}
                      {isRevoked && (
                        <span className="block truncate text-[13px] text-ink-muted">
                          Policy revoked
                        </span>
                      )}
                    </span>
                    {isActive ? (
                      // A real drill-in to Card detail — the rotated back
                      // chevron reads correctly as "view more" here.
                      <span className="rotate-180 shrink-0 text-ink-muted">
                        <BackChevronIcon size={18} strokeWidth={2} />
                      </span>
                    ) : (
                      // Neither a no-policy nor a revoked card is a detail
                      // view to browse — both need a new policy next, so
                      // both say that instead of a chevron (D-051, D-052).
                      // A revoked row still opens Card detail on tap (its
                      // checks/history stay reachable, D-044), where the
                      // customer finds the actual "Add policy" button.
                      <span className="flex shrink-0 items-center gap-1 text-[13px] font-semibold text-ink">
                        <PlusIcon size={14} strokeWidth={2.4} />
                        Add policy
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        ))}

      <p className="text-[13px] text-ink-muted">
        Bank limits on your accounts and cards are separate from your own spending policy — your
        policy is always the stricter of the two.
      </p>
    </div>
  )
}
