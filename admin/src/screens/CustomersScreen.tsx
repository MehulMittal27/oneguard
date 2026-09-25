import { useEffect, useState } from 'react'
import type { Account, Customer, Mandate } from '../api/types'
import { getAccounts, getPolicy } from '../api/customers'
import { Badge } from '../components/Badge'
import { EmptyState, ErrorState, Spinner } from '../components/StateViews'

/**
 * Read-only reference (C12/C10/C3): who the ten fixture customers are, what
 * accounts and cards sit under them, and whether a card has an active
 * policy — context for picking a card in the Scenarios screen, never a place
 * that writes anything.
 */
export function CustomersScreen({ customers, loading, error }: { customers: Customer[]; loading: boolean; error: unknown }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[20px] font-semibold text-ink">Customers</h1>
        <p className="text-[13px] text-ink-muted">Every customer the store knows (C12), for reference — never edited here.</p>
      </div>

      {loading && <Spinner />}
      {error !== null && error !== undefined && <ErrorState error={error} />}
      {!loading && customers.length === 0 && <EmptyState>No customers yet. They appear once the worker syncs Viseca's reference data.</EmptyState>}

      <div className="space-y-2">
        {customers.map((c) => (
          <div key={c.customer_id} className="rounded-card border border-hairline bg-surface">
            <button
              type="button"
              onClick={() => setExpanded(expanded === c.customer_id ? null : c.customer_id)}
              className="flex w-full items-center gap-3 px-5 py-3 text-left"
            >
              <span className="flex-1">
                <span className="block text-[14px] font-semibold text-ink">{c.name}</span>
                <span className="block text-[11.5px] tabular-nums text-ink-muted">
                  {c.customer_id} · {c.home_region}
                </span>
              </span>
              {c.live && <Badge tone="accent">Live</Badge>}
              {c.scenario_ids.length > 0 && <Badge tone="neutral">{c.scenario_ids.join(', ')}</Badge>}
            </button>
            {expanded === c.customer_id && <CustomerDetail customer={c} />}
          </div>
        ))}
      </div>
    </div>
  )
}

function CustomerDetail({ customer }: { customer: Customer }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [policies, setPolicies] = useState<Record<string, Mandate | null>>({})

  useEffect(() => {
    let cancelled = false
    getAccounts(customer.customer_id).then((rows) => {
      if (cancelled) return
      setAccounts(rows)
      for (const account of rows) {
        for (const card of account.cards) {
          getPolicy(card.card_id).then((mandate) => {
            if (!cancelled) setPolicies((prev) => ({ ...prev, [card.card_id]: mandate }))
          })
        }
      }
    })
    return () => {
      cancelled = true
    }
  }, [customer.customer_id])

  if (accounts === null) return <div className="border-t border-hairline px-5 py-4"><Spinner /></div>

  return (
    <div className="space-y-3 border-t border-hairline px-5 py-4">
      {accounts.map((account) => (
        <div key={account.account_id} className="text-[13px]">
          <p className="font-medium text-ink-soft">
            {account.account_type} · {account.account_purpose} · {account.status}
          </p>
          <div className="mt-1.5 space-y-1.5">
            {account.cards.map((card) => {
              const mandate = policies[card.card_id]
              return (
                <div key={card.card_id} className="flex flex-wrap items-center gap-2 rounded-tile bg-surface-sunken px-3 py-2">
                  <span className="font-semibold tabular-nums text-ink">{card.card_id}</span>
                  <span className="text-ink-muted">{card.card_type} · {card.status}</span>
                  {mandate === undefined ? (
                    <span className="text-ink-muted">checking policy…</span>
                  ) : mandate && mandate.status === 'active' ? (
                    <Badge tone="approved">Active policy: {mandate.instruction.length > 40 ? `${mandate.instruction.slice(0, 40)}…` : mandate.instruction}</Badge>
                  ) : (
                    <Badge tone="neutral">No active policy</Badge>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
