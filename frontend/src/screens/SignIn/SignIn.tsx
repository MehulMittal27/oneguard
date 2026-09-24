import { useEffect, useState } from 'react'
import { getCustomers } from '../../api/customers'
import type { Customer } from '../../api/types'
import { BottomSheet } from '../../components/BottomSheet'
import { DeviceFrame } from '../../components/DeviceFrame'
import { ShieldIcon } from '../../components/icons/lucide'
import { StatusBar } from '../../components/StatusBar'
import { splitSignInCustomers } from '../../lib/signInCustomers'
import { useCustomer } from '../../state/CustomerContext'

const CORD_STEPS = ['Agent proposes', 'Your rules decide', 'Money moves, or not']
const MAX_VISIBLE_CUSTOMERS = 4

type Status = 'loading' | 'error' | 'ready'

/**
 * DESIGN.md #1 — sign in. A light screen on the page ground, not the ink hero
 * it used to be: the recalibrated theme has no dark panels, and the mockup
 * carries the brand with a black logo tile and one accent-coloured step instead.
 * The three steps are joined by a straight cord rather than the old curve, so
 * the numbered discs line up with their labels on a 390px screen.
 */
export function SignIn() {
  const { continueAs } = useCustomer()
  const [status, setStatus] = useState<Status>('loading')
  const [customers, setCustomers] = useState<Customer[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [showOthers, setShowOthers] = useState(false)

  useEffect(() => {
    let cancelled = false
    getCustomers()
      .then((result) => {
        if (cancelled) return
        setCustomers(result)
        setStatus('ready')
      })
      .catch(() => {
        if (cancelled) return
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  const selected = customers.find((c) => c.customer_id === selectedId) ?? null
  const { visible: visibleCustomers, others: otherCustomers } = splitSignInCustomers(
    customers,
    selectedId,
    MAX_VISIBLE_CUSTOMERS,
  )

  return (
    <main>
      <DeviceFrame>
        <div className="flex min-h-0 flex-1 flex-col bg-ground">
          <StatusBar tone="ink" />
          <div className="scrollbar-none flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-8 pt-4 pb-9 sm:pt-5">
            <header className="flex flex-col gap-4">
              <div className="flex items-center gap-4">
                <span className="flex size-[34px] items-center justify-center rounded-tile bg-ink text-on-ink">
                  <ShieldIcon size={20} strokeWidth={1.8} />
                </span>
                <p className="text-[11px] font-semibold tracking-[0.09em] text-ink-soft uppercase">
                  OneGuard
                </p>
              </div>
              <h1 className="font-display text-[34px] leading-[1.05] font-bold tracking-[-0.02em] text-ink">
                Your AI agent shops.
                <br />
                You hold the leash.
              </h1>
              <p className="max-w-[290px] text-[15px] leading-[1.45] text-ink-soft">
                Every purchase your agent proposes passes through your rules first.
              </p>
            </header>

            {/* How it works — not a decision, purely the sign-in explainer strip. */}
            <div className="relative pt-1">
              <span
                aria-hidden="true"
                className="absolute top-[14px] right-[58px] left-[58px] h-1 rounded-pill bg-cord-accent"
              />
              <div className="relative grid grid-cols-3 gap-3">
                {CORD_STEPS.map((label, i) => (
                  <div key={label} className="flex flex-col items-center gap-3 text-center">
                    <span
                      aria-hidden="true"
                      className={`flex size-8 items-center justify-center rounded-full text-[14px] font-semibold ${
                        i === 1 ? 'bg-cord-accent text-on-ink' : 'bg-ink text-on-ink'
                      }`}
                    >
                      {i + 1}
                    </span>
                    <p className="text-[13px] leading-[1.25] font-semibold text-ink">{label}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-3">
              <h2 className="text-[11px] font-semibold tracking-[0.09em] text-ink-muted uppercase">
                Sign in as · demo, no password
              </h2>

              <CustomerList
                status={status}
                customers={visibleCustomers}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onRetry={() => {
                  setStatus('loading')
                  setAttempt((n) => n + 1)
                }}
              />

              {status === 'ready' && otherCustomers.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowOthers(true)}
                  className="min-h-11 self-start text-left text-[13px] text-ink-muted underline decoration-border-quiet underline-offset-4"
                >
                  + {otherCustomers.length} more customers
                </button>
              )}
            </div>

            <div className="mt-auto flex flex-col gap-4 pt-2">
              <button
                type="button"
                disabled={!selected}
                onClick={() => selected && continueAs(selected)}
                className="h-14 rounded-row bg-ink font-sans text-[16px] font-semibold text-on-ink transition-opacity disabled:cursor-not-allowed disabled:bg-border-quiet disabled:text-ink-muted enabled:hover:opacity-90"
              >
                {selected ? `Continue as ${selected.name}` : 'Continue'}
              </button>
              <p className="text-center text-[12px] text-ink-muted">
                Demo sign-in · synthetic data · no real money
              </p>
            </div>
          </div>
        </div>

        {showOthers && (
          <BottomSheet
            title="Choose another customer"
            onClose={() => setShowOthers(false)}
            footer={
              <button
                type="button"
                onClick={() => setShowOthers(false)}
                className="h-14 w-full rounded-row bg-ink font-sans text-[16px] font-semibold text-on-ink"
              >
                Done
              </button>
            }
          >
            <CustomerList
              status="ready"
              customers={otherCustomers}
              selectedId={selectedId}
              onSelect={setSelectedId}
              ariaLabel="Choose another customer"
            />
          </BottomSheet>
        )}
      </DeviceFrame>
    </main>
  )
}

/**
 * One list, one treatment: the screen and the "more customers" sheet are both
 * light now, so the old dark/light variant split is gone.
 *
 * The mockup's secondary line reads "Zurich region · careful". There is no
 * budget-style field on `Customer` (`../docs/api-contract.md` §2), so the row
 * shows the region and the customer id — both real — rather than inventing the
 * missing half.
 */
function CustomerList({
  status,
  customers,
  selectedId,
  onSelect,
  onRetry,
  ariaLabel = 'Choose a customer',
}: {
  status: Status
  customers: Customer[]
  selectedId: string | null
  onSelect: (id: string) => void
  onRetry?: () => void
  ariaLabel?: string
}) {
  if (status === 'loading') {
    return (
      <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-[60px] animate-pulse rounded-row bg-surface-sunken" />
        ))}
        <span className="sr-only">Loading customers</span>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="flex flex-col items-start gap-4 rounded-row border border-hairline bg-surface p-5">
        {/* README.md §5.9: every error state says what is safe, not just that
            something failed. Nobody is signed in yet, so nothing has been
            decided on anyone's behalf — say that. */}
        <p className="text-[15px] text-ink-soft">
          Couldn&apos;t load your accounts. Nothing was approved while we were offline. No
          purchase is decided until you are signed in and your rules are live.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="h-11.5 rounded-button border-2 border-ink px-5 text-[15px] font-semibold text-ink"
        >
          Try again
        </button>
      </div>
    )
  }

  return (
    <div role="radiogroup" aria-label={ariaLabel} className="flex flex-col gap-3">
      {customers.map((customer) => {
        const isSelected = customer.customer_id === selectedId
        return (
          <label
            key={customer.customer_id}
            className={`flex min-h-[60px] cursor-pointer items-center gap-4 rounded-row border-2 bg-surface px-5 py-3 transition-colors ${
              isSelected ? 'border-ink' : 'border-transparent'
            }`}
          >
            <input
              type="radio"
              name="customer"
              value={customer.customer_id}
              checked={isSelected}
              onChange={() => onSelect(customer.customer_id)}
              className="size-5 shrink-0 accent-ink"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-display text-[17px] font-bold text-ink">
                {customer.name}
              </span>
              <span className="block truncate text-[13px] text-ink-muted">
                {customer.home_region}
              </span>
            </span>
            <span className="flex shrink-0 flex-col items-end gap-1">
              <span className="text-[12px] tracking-[0.04em] text-ink-muted">
                {customer.customer_id}
              </span>
              {customer.live ? (
                <span className="rounded-pill bg-approved-tint px-[9px] py-[3px] text-[12px] font-semibold text-approved">
                  Live
                </span>
              ) : (
                <span className="text-[12px] text-ink-muted">No scenario yet</span>
              )}
            </span>
          </label>
        )
      })}
    </div>
  )
}
