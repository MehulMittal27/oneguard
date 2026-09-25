import { useEffect, useState } from 'react'
import type { Customer } from '../api/types'
import { getAccounts } from '../api/customers'
import { getLedger } from '../api/ledger'
import { usePolling } from '../lib/usePolling'
import { RUN_POLL_SECONDS } from '../config'
import { formatChf } from '../lib/money'
import { formatSimTime } from '../lib/datetime'
import { Badge } from '../components/Badge'
import { EmptyState, ErrorState, Spinner } from '../components/StateViews'

/** Every card across every customer, flattened once accounts are read. */
function useAllCards(customers: Customer[]) {
  const [cards, setCards] = useState<{ card_id: string; customer_name: string }[]>([])
  useEffect(() => {
    let cancelled = false
    Promise.all(customers.map((c) => getAccounts(c.customer_id).then((accounts) => ({ c, accounts }))))
      .then((rows) => {
        if (cancelled) return
        setCards(rows.flatMap(({ c, accounts }) => accounts.flatMap((a) => a.cards.map((card) => ({ card_id: card.card_id, customer_name: c.name })))))
      })
      .catch(() => {
        if (!cancelled) setCards([])
      })
    return () => {
      cancelled = true
    }
  }, [customers])
  return cards
}

/** D6 — "the ledger of the card's latest run, as the engine counts it": the
 * reproduce-this-decision view, for when a number on screen needs checking
 * against the engine's own running total rather than trusting the platform. */
export function LedgerScreen({ customers }: { customers: Customer[] }) {
  const cards = useAllCards(customers)
  // Derived, not synced via an effect (same reasoning as DecisionsScreen's
  // customerId): an explicit pick wins, falling back to the first card once
  // the async account reads land.
  const [manualCardId, setManualCardId] = useState('')
  const cardId = manualCardId || cards[0]?.card_id || ''

  const { data: ledger, error, loading, refresh } = usePolling(
    () => (cardId ? getLedger(cardId) : Promise.reject(new Error('no card selected'))),
    RUN_POLL_SECONDS,
    [cardId],
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[20px] font-semibold text-ink">Ledger</h1>
        <p className="text-[13px] text-ink-muted">The engine's own running total for a card's latest run (D6).</p>
      </div>

      <select
        value={cardId}
        onChange={(e) => setManualCardId(e.target.value)}
        className="rounded-tile border border-border-quiet bg-surface px-3 py-2 text-[13px] tabular-nums text-ink outline-none focus:border-cord-accent"
      >
        {cards.length === 0 && <option value="">No cards yet</option>}
        {cards.map((c) => (
          <option key={c.card_id} value={c.card_id}>
            {c.card_id} — {c.customer_name}
          </option>
        ))}
      </select>

      {loading && !ledger && <Spinner />}
      {error && cardId ? <ErrorState error={error} onRetry={refresh} /> : null}

      {ledger && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-card border border-hairline bg-surface p-4">
            <span className="text-[13px] text-ink-muted">Mandate</span>
            <span className="text-[13px] font-semibold tabular-nums text-ink">{ledger.mandate_id || '—'}</span>
            <span className="ml-auto text-[13px] text-ink-muted">Period spend</span>
            <span className="text-[15px] font-semibold tabular-nums text-ink">{formatChf(ledger.period_spent_chf)}</span>
            {ledger.frozen && <Badge tone="stopped">Frozen</Badge>}
          </div>

          {ledger.entries.length === 0 ? (
            <EmptyState>No entries on this card's latest run yet.</EmptyState>
          ) : (
            <div className="overflow-x-auto rounded-card border border-hairline bg-surface">
              <table className="w-full text-left text-[13px]">
                <thead>
                  <tr className="border-b border-hairline text-[11px] tracking-[0.04em] text-ink-muted uppercase">
                    <th className="px-4 py-2.5 font-semibold">Authorization</th>
                    <th className="px-4 py-2.5 font-semibold">Occurred</th>
                    <th className="px-4 py-2.5 font-semibold">Decision</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Counted</th>
                    <th className="px-4 py-2.5 font-semibold">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.entries.map((e) => (
                    <tr key={e.authorization_id} className="border-b border-hairline last:border-0">
                      <td className="px-4 py-2.5 tabular-nums text-ink-soft">{e.authorization_id}</td>
                      <td className="px-4 py-2.5 tabular-nums text-ink-muted">{formatSimTime(e.occurred_at)}</td>
                      <td className="px-4 py-2.5 text-ink">{e.decision}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-ink">{formatChf(e.counted_chf)}</td>
                      <td className="px-4 py-2.5 text-ink-muted">{e.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
