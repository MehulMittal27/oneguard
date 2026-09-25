import type { CurrentRun, Customer, Decision } from '../api/types'
import { getCurrentRun } from '../api/runs'
import { getDecisions } from '../api/decisions'
import { usePolling } from '../lib/usePolling'
import { customerForCard } from '../lib/customerLookup'
import { RUN_POLL_SECONDS, DECISIONS_POLL_SECONDS } from '../config'
import { Badge } from './Badge'
import { ProgressBar } from './ProgressBar'
import { DecisionRow } from './DecisionRow'
import { EmptyState, ErrorState, Spinner } from './StateViews'

const RUN_STATE_TONE = { starting: 'asked', running: 'accent', done: 'approved', error: 'stopped' } as const

async function decisionsFor(customerId: string | undefined): Promise<Decision[]> {
  if (!customerId) return []
  return getDecisions(customerId)
}

function currentCustomer(current: CurrentRun | null, customers: Customer[]): { customer_id: string; name: string } | undefined {
  if (!current) return undefined
  if (current.kind === 'live' && current.run.customer_id) {
    return { customer_id: current.run.customer_id, name: current.run.customer_name ?? current.run.customer_id }
  }
  const byCard = customerForCard(current.run.card_id, customers)
  return byCard ? { customer_id: byCard.customer_id, name: byCard.name } : undefined
}

/**
 * D7 — "the newest run, live or replay, by the real time it started; starts
 * nothing" — polled here so the console always shows whatever run is
 * actually in progress at the platform, regardless of which scenario is
 * selected below. This is the "step by step" view: progress ticks up and
 * decisions append as the worker (or the offline runner) delivers them.
 */
export function NowRunningPanel({ customers }: { customers: Customer[] }) {
  const { data: current, error, loading } = usePolling(getCurrentRun, RUN_POLL_SECONDS, [])
  const customer = currentCustomer(current, customers)

  const { data: decisions } = usePolling(
    () => decisionsFor(customer?.customer_id),
    DECISIONS_POLL_SECONDS,
    [customer?.customer_id],
  )

  const feed =
    current?.kind === 'live'
      ? (decisions ?? []).filter((d) => d.run_id === current.run.run_id)
      : current?.kind === 'replay'
        ? (decisions ?? []).filter((d) => d.card_id === current.run.card_id).slice(0, Math.max(current.run.delivered, 1))
        : []

  return (
    <section className="rounded-card border border-hairline bg-surface p-5">
      <h2 className="mb-3 text-[13px] font-semibold tracking-[0.04em] text-ink-muted uppercase">Now running</h2>

      {loading && !current && <Spinner />}
      {error ? <ErrorState error={error} /> : null}

      {!loading && !error && !current && (
        <EmptyState>No run has started yet. Launch one from the scenario catalogue below.</EmptyState>
      )}

      {current?.kind === 'live' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={RUN_STATE_TONE[current.run.state]}>{current.run.state}</Badge>
            <span className="text-[12.5px] tabular-nums text-ink-muted">
              {current.run.scenario_id} · run {current.run.run_id} · card {current.run.card_id}
            </span>
            {customer?.name && <span className="text-[12.5px] text-ink-muted">— {customer.name}</span>}
            {!current.run.worker_ok && <Badge tone="stopped">Worker degraded</Badge>}
          </div>
          <ProgressBar decided={current.run.decided} pendingHuman={current.run.pending_human} total={current.run.total} />
          <p className="text-[12px] tabular-nums text-ink-muted">
            {current.run.decided}/{current.run.total} decided · {current.run.pending_human} waiting for the customer
          </p>
          {current.run.last_error && <p className="text-[12px] text-stopped">{current.run.last_error}</p>}
        </div>
      )}

      {current?.kind === 'replay' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={current.run.running ? 'accent' : 'neutral'}>{current.run.running ? 'running' : 'idle'}</Badge>
            <span className="text-[12.5px] tabular-nums text-ink-muted">
              {current.run.scenario_id} · card {current.run.card_id} (offline)
            </span>
            {customer?.name && <span className="text-[12.5px] text-ink-muted">— {customer.name}</span>}
          </div>
          <ProgressBar decided={current.run.delivered} pendingHuman={0} total={current.run.total} />
          <p className="text-[12px] tabular-nums text-ink-muted">
            {current.run.delivered}/{current.run.total} delivered
          </p>
        </div>
      )}

      {current && (
        <div className="mt-4 space-y-2">
          {!customer && <p className="text-[12px] text-ink-muted">Card not yet linked to a known customer — decisions will appear once it is.</p>}
          {customer && feed.length === 0 && <p className="text-[12px] text-ink-muted">No decisions delivered yet.</p>}
          {feed.map((d) => (
            <DecisionRow key={d.authorization_id} decision={d} />
          ))}
        </div>
      )}
    </section>
  )
}
