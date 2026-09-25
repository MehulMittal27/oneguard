import type { Decision } from './types'
import { HUMAN_WINDOW_SECONDS } from '../config'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

// Demo pacing only, not real data: seconds left on each pending mock row's
// countdown at the moment it's first read, so a demo can show a fresh
// countdown, a mid one, and one about to expire without waiting the full
// 120s human window on all three. Falls back to the full window for any
// pending row not listed here. A real backend would just send `deadline_at`
// on the decision itself; this mimics that for the mock fixture.
const DEMO_SECONDS_REMAINING: Record<string, number> = {
  AU0006: 100,
  AU0018: 45,
  AU0040: 12,
  // The two beats that need reading time, not urgency: the remembered-answer
  // step-up and the session watch both carry a sentence the customer has to
  // take in before deciding.
  AU0022: 110,
  AU0031: 115,
}

function withDeadline(decision: Decision): Decision {
  if (decision.status !== 'pending_human') return decision
  const remaining = DEMO_SECONDS_REMAINING[decision.authorization_id] ?? HUMAN_WINDOW_SECONDS
  return { ...decision, deadline_at: new Date(Date.now() + remaining * 1000).toISOString() }
}

/**
 * Lists a customer's decisions for the Activity screen and the step-up
 * inbox alike (contract capability C6, endpoint proposed — not yet
 * backend-ratified) — a pending step-up is just a decision with
 * `status: 'pending_human'`, not a separate resource (C7 was proposed and
 * then folded back into this single endpoint, see decisions.md). In mock
 * mode this resolves from a local fixture instead of a real request;
 * swapping to the backend once it exists is deleting the `if` branch, not
 * touching call sites. Call this once per sign-in (DecisionsProvider), not
 * on every screen visit — `deadline_at` uses the real clock, so refetching
 * would keep resetting any pending countdown.
 *
 * `operator: true` (the `/ops` console only) adds `?operator=1`, so C6 also
 * sends the operator-only evidence rows (ledger reconciliation,
 * `../../docs/api-contract.md` §3.4). The customer's app never asks for them.
 */
export async function getDecisions(customerId: string, { operator = false }: { operator?: boolean } = {}): Promise<Decision[]> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    const { decisions } = await import('../mocks/fixtures/decisions.json')
    return (decisions as Decision[])
      .filter((d) => d.customer_id === customerId)
      .map(withDeadline)
  }

  const query = operator ? '?operator=1' : ''
  const response = await fetch(`${API_BASE_URL}/customers/${customerId}/decisions${query}`)
  if (!response.ok) {
    throw new Error(`Failed to load decisions (${response.status})`)
  }
  const data = (await response.json()) as { decisions: Decision[] }
  return data.decisions
}
