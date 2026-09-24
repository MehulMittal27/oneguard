import { useEffect, useState } from 'react'
import { getCustomers } from '../../api/customers'
import type { Customer } from '../../api/types'
import { BottomSheet } from '../../components/BottomSheet'
import { DeviceFrame } from '../../components/DeviceFrame'
import { StatusBar } from '../../components/StatusBar'
import { useCustomer } from '../../state/CustomerContext'

const CORD_STEPS = ['Agent proposes', 'Your rules decide', 'Money moves, or not']
const MAX_VISIBLE_CUSTOMERS = 4

type Status = 'loading' | 'error' | 'ready'

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
  const liveCustomers = customers.filter((c) => c.live)
  const otherCustomers = customers.filter((c) => !c.live)
  const visibleCustomers = selected && !selected.live
    ? [selected, ...liveCustomers].slice(0, MAX_VISIBLE_CUSTOMERS)
    : liveCustomers.slice(0, MAX_VISIBLE_CUSTOMERS)

  return (
    <main>
      <DeviceFrame>
        <div className="signin-backdrop flex min-h-0 flex-1 flex-col">
          <StatusBar tone="on-ink" />
          <div className="scrollbar-none flex min-h-0 flex-1 flex-col gap-8 overflow-y-auto px-8 pt-14 pb-9 sm:pt-5">
            <header>
              <p className="font-sans text-[12px] font-semibold tracking-[0.1em] text-on-ink-muted uppercase">
                Agent on a
              </p>
              <p className="font-display text-[40px] leading-[0.95] font-bold text-on-ink">Leash</p>
            </header>

            <p className="text-[14px] leading-5.5 text-on-ink-soft">
              Your rules decide what your agent can buy. Sign in as a customer to see it in
              action.
            </p>

            {/* Cord diagram — not a decision, purely the sign-in "how it works" strip. */}
            <div>
              <svg viewBox="0 0 260 60" className="w-full" style={{ height: 60 }} aria-hidden="true">
                <path
                  d="M12 46 C 70 4, 120 4, 130 30 S 200 56, 248 14"
                  fill="none"
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  className="stroke-cord-accent-on-ink"
                />
                <circle cx="12" cy="46" r="5" className="fill-on-ink" />
                <circle cx="130" cy="30" r="7" className="fill-cord-accent-on-ink" />
                <circle cx="248" cy="14" r="5" className="fill-on-ink" />
              </svg>
              <div className="mt-1 flex justify-between">
                {CORD_STEPS.map((label, i) => (
                  <p
                    key={label}
                    className={`max-w-[86px] text-[10px] leading-3.5 font-semibold tracking-[0.06em] uppercase ${
                      i === 1 ? 'text-center text-cord-accent-on-ink' : 'text-on-ink-muted'
                    } ${i === 2 ? 'text-right' : ''}`}
                  >
                    {label}
                  </p>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-6">
              <div>
                <h1 className="mb-3 font-display text-[20px] font-bold text-on-ink">
                  Continue as
                </h1>

                <CustomerList
                  variant="dark"
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
                    className="mt-3 min-h-11 text-[15px] font-semibold text-on-ink underline decoration-on-ink-muted underline-offset-4"
                  >
                    Select other
                  </button>
                )}
              </div>

              <button
                type="button"
                disabled={!selected}
                onClick={() => selected && continueAs(selected)}
                className="h-14 rounded-row bg-cord-accent-on-ink font-sans text-[16px] font-semibold text-ink transition-opacity disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-on-ink-muted enabled:hover:opacity-90"
              >
                {selected ? `Continue as ${selected.name}` : 'Continue'}
              </button>
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
              variant="light"
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

function CustomerList({
  variant,
  status,
  customers,
  selectedId,
  onSelect,
  onRetry,
  ariaLabel = 'Choose a customer',
}: {
  // 'dark' renders on SignIn's own ink background; 'light' renders inside
  // the (always-light) BottomSheet for "Select other" — same component,
  // same behavior, two color treatments so neither call site has to
  // reimplement the list.
  variant: 'dark' | 'light'
  status: Status
  customers: Customer[]
  selectedId: string | null
  onSelect: (id: string) => void
  onRetry?: () => void
  ariaLabel?: string
}) {
  const isDark = variant === 'dark'

  if (status === 'loading') {
    return (
      <div className="flex flex-col gap-3" aria-live="polite" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className={`h-16 animate-pulse rounded-row ${isDark ? 'bg-on-ink-rule' : 'bg-surface-sunken'}`}
          />
        ))}
        <span className="sr-only">Loading customers</span>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div
        className={`flex flex-col items-start gap-4 rounded-row p-5 ${
          isDark ? 'border border-on-ink-rule bg-white/5' : 'border border-hairline bg-surface'
        }`}
      >
        <p className={`text-[15px] ${isDark ? 'text-on-ink-soft' : 'text-ink-soft'}`}>
          Couldn&apos;t load your accounts. Check your connection and try again.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className={`h-11.5 rounded-button border-2 px-5 text-[15px] font-semibold ${
            isDark ? 'border-on-ink text-on-ink' : 'border-ink text-ink'
          }`}
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
            className={`flex min-h-16 cursor-pointer items-center gap-4 rounded-row px-5 py-3 transition-colors ${
              isDark
                ? isSelected
                  ? 'border-[1.5px] border-cord-accent-on-ink bg-white/5'
                  : 'border border-on-ink-rule'
                : isSelected
                  ? 'border-2 border-ink'
                  : 'border border-hairline'
            }`}
          >
            <input
              type="radio"
              name="customer"
              value={customer.customer_id}
              checked={isSelected}
              onChange={() => onSelect(customer.customer_id)}
              className={`size-5 ${isDark ? 'accent-cord-accent-on-ink' : 'accent-ink'}`}
            />
            <span className="min-w-0 flex-1">
              <span
                className={`block truncate text-[15px] font-semibold ${isDark ? 'text-on-ink' : 'text-ink'}`}
              >
                {customer.name}
              </span>
              <span
                className={`block truncate text-[13px] ${isDark ? 'text-on-ink-muted' : 'text-ink-muted'}`}
              >
                {customer.home_region}
              </span>
            </span>
            {customer.live ? (
              <span
                className={`shrink-0 rounded-pill px-3 py-1 text-[13px] font-medium ${
                  isDark ? 'bg-cord-accent-on-ink text-ink' : 'bg-approved-tint text-approved'
                }`}
              >
                Live
              </span>
            ) : (
              <span className={`shrink-0 text-[13px] ${isDark ? 'text-on-ink-muted' : 'text-ink-muted'}`}>
                No scenario yet
              </span>
            )}
          </label>
        )
      })}
    </div>
  )
}
