import { useEffect, useState } from 'react'
import { getAccounts } from '../../api/accounts'
import type { Account } from '../../api/types'
import { BottomSheet } from '../../components/BottomSheet'
import { BackChevronIcon } from '../../components/icons/lucide'
import { NetworkState } from '../../components/NetworkState'
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
      className={`relative h-[34px] w-[52px] shrink-0 rounded-meter ${guarded ? 'bg-ink' : 'bg-card-icon-gray'
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
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null)
  const [accountPickerOpen, setAccountPickerOpen] = useState(false)

  useEffect(() => {
    if (!signedInAs) return
    let cancelled = false
    getAccounts(signedInAs.customer_id)
      .then((result) => {
        if (cancelled) return
        setAccounts(result)
        setSelectedAccountId((selected) => selected ?? result[0]?.account_id ?? null)
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
          {accounts.length > 1
            ? 'Choose an account, then a card. Policies belong to a card.'
            : 'Policies belong to a card, not to you as a whole. Each card is guarded on its own.'}
        </p>
      </div>

      {status === 'loading' && (
        <NetworkState kind="loading" label="your accounts" />
      )}

      {status === 'error' && (
        <NetworkState
          kind="error"
          label="your accounts"
          onRetry={() => {
            if (accountsStatus === 'error') {
              setAccountsStatus('loading')
              setAttempt((n) => n + 1)
            }
            if (policiesStatus === 'error') retryPolicies()
          }}
        />
      )}

      {status === 'ready' && accounts.length === 0 && (
        <NetworkState kind="empty" label="your accounts">
          No accounts are available for this customer yet.
        </NetworkState>
      )}

      {status === 'ready' && selectedAccountId && (() => {
        const selectedAccount = accounts.find((account) => account.account_id === selectedAccountId) ?? accounts[0]
        if (!selectedAccount) return null
        const selectedIndex = accounts.findIndex((account) => account.account_id === selectedAccount.account_id)
        const guardedAccountCount = accounts.filter((account) =>
          account.cards.some((card) => policiesByCard[card.card_id]?.status === 'active'),
        ).length
        const otherUnprotected = accounts.find((account) =>
          account.account_id !== selectedAccount.account_id &&
          !account.cards.some((card) => policiesByCard[card.card_id]?.status === 'active'),
        )
        return (
          <>
            {accounts.length > 1 && (
              <button
                type="button"
                onClick={() => setAccountPickerOpen(true)}
                className="flex min-h-[72px] w-full flex-col items-start justify-center gap-1 rounded-card border border-hairline bg-surface px-5 py-4 text-left"
              >
                <span className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">Account · {selectedIndex + 1} of {accounts.length}</span>
                <span className="flex w-full items-center justify-between gap-3">
                  <span className="truncate text-[15px] font-medium text-ink">{humanise(selectedAccount.account_purpose)} · {selectedAccount.account_type.replace(/_/g, ' ')} · {selectedAccount.account_id}</span>
                  <span className="shrink-0 text-[13px] font-semibold text-ink-muted underline underline-offset-2">Change</span>
                </span>
              </button>
            )}

            <div className="rounded-card bg-surface p-5">
              <div className="flex w-full items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-display text-[19px] font-bold text-ink">{humanise(selectedAccount.account_purpose)}</p>
                </div>
                {accounts.length > 1 && selectedAccount.status === 'active' && (
                  <span className="shrink-0 rounded-pill bg-approved-tint px-2.5 py-1 text-[12px] font-semibold text-approved">Active</span>
                )}
              </div>

              <div className="mt-4 flex flex-col gap-2">
                <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">Cards on this account</p>
                {selectedAccount.cards.map((card) => {
                  const mandate = policiesByCard[card.card_id]
                  const isActive = mandate?.status === 'active'
                  const isRevoked = mandate?.status === 'revoked'
                  const limitCount = mandate
                    ? [limitsFromMandate(mandate).perOrder, limitsFromMandate(mandate).period].filter(Boolean).length
                    : 0
                  return (
                    <button
                      key={card.card_id}
                      type="button"
                      onClick={() => (isActive || isRevoked ? onViewCard(card.card_id) : onAddPolicy(card.card_id))}
                      className={`flex min-h-[72px] items-center gap-3 rounded-[18px] p-3 text-left ${isActive ? 'border-2 border-ink' : 'border-2 border-dashed border-border-dashed'}`}
                    >
                      <CardChip guarded={isActive} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[15px] font-semibold text-balance text-ink">{humanise(card.card_purpose)} · {card.card_id}</span>
                        {isActive && (
                          <span className="block text-[13px] leading-[1.35] text-ink-muted">
                            {humanise(card.card_type)} · {limitCount} {limitCount === 1 ? 'limit' : 'limits'}
                          </span>
                        )}
                      </span>
                      <span className="flex shrink-0 items-center gap-1">
                        <span className={`rounded-pill px-2.5 py-1 text-[12px] font-semibold ${isActive ? 'bg-approved-tint text-approved' : 'bg-surface-expired text-ink-muted'}`}>
                          {isActive ? 'Policy active' : isRevoked ? 'Policy revoked' : 'No policy'}
                        </span>
                        <span className="rotate-180 text-ink-muted" aria-hidden="true">
                          <BackChevronIcon size={18} strokeWidth={2} />
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            {otherUnprotected && (
              <p className="rounded-row bg-surface-active px-5 py-4 text-[13px] leading-[1.45] text-ink-soft">
                Your other account, {humanise(otherUnprotected.account_purpose)} ({otherUnprotected.account_id}), has no policy. Switch account above to add one.
              </p>
            )}
            {accounts.length > 1 && <p className="text-center text-[13px] text-ink-muted">{guardedAccountCount} of {accounts.length} accounts have a guarded card.</p>}

            {accountPickerOpen && (
              <BottomSheet
                title="Choose an account"
                onClose={() => setAccountPickerOpen(false)}
                footer={<button type="button" onClick={() => setAccountPickerOpen(false)} className="h-14 w-full rounded-row bg-ink text-[16px] font-semibold text-on-ink">Done</button>}
              >
                <div className="flex flex-col gap-2">
                  {accounts.map((account) => {
                    const guarded = account.cards.filter((card) => policiesByCard[card.card_id]?.status === 'active').length
                    const selected = account.account_id === selectedAccount.account_id
                    return (
                      <button
                        key={account.account_id}
                        type="button"
                        aria-pressed={selected}
                        onClick={() => setSelectedAccountId(account.account_id)}
                        className={`flex min-h-16 flex-col items-start justify-center rounded-row border-2 px-4 py-3 text-left ${selected ? 'border-ink bg-surface-sunken' : 'border-hairline bg-surface'}`}
                      >
                        <span className="text-[15px] font-semibold text-ink">{humanise(account.account_purpose)}</span>
                        <span className="text-[13px] text-ink-muted">{humanise(account.account_type)} · {account.account_id}</span>
                        <span className="text-[12px] text-ink-muted">{guarded} of {account.cards.length} {account.cards.length === 1 ? 'card' : 'cards'} guarded{guarded === 0 ? ' · agent blocked' : ''}</span>
                      </button>
                    )
                  })}
                  <p className="py-2 text-[13px] leading-[1.45] text-ink-muted">Each card has its own policy. Switching accounts changes what you see here, not what your agent may do.</p>
                </div>
              </BottomSheet>
            )}
          </>
        )
      })()}
    </div>
  )
}
