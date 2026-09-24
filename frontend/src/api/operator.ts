import type { LiveRun, ReplayStatus } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * The operator endpoints (`../docs/api-contract.md` §1.2 D1–D7). These exist for
 * the person driving the demo, never for the customer: nothing here is reachable
 * from a customer-facing screen, and the strip that calls them is behind
 * `?demo=1`.
 *
 * Same mock/real branch as every other resource module, so swapping to a real
 * backend is deleting the mock arm, not rewriting callers. There is no operator
 * fixture: with mocks on these resolve to "nothing running", because in mock
 * mode nothing *is* running — the decisions come from a fixture, not a replay.
 * Inventing counters here would put a number on screen that stands for nothing.
 */
export type CurrentRun = { kind: 'live'; run: LiveRun } | { kind: 'replay'; run: ReplayStatus }

/**
 * D7: the newest run, live (D3's `LiveRun`) or offline replay (D2's
 * `ReplayStatus`), told apart by `run_id`, which only a live run has. 404 means
 * no run has started, which is `null` here, not an error.
 */
export async function getCurrentRun(): Promise<CurrentRun | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return null

  const response = await fetch(`${API_BASE_URL}/dev/runs/current`)
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`Failed to read current run (${response.status})`)
  const body = (await response.json()) as LiveRun | ReplayStatus
  return 'run_id' in body ? { kind: 'live', run: body } : { kind: 'replay', run: body }
}

/**
 * D5, the chaos toggle: turns the small decision model off and on at runtime.
 * The engine must reach the same outcomes without it, or more cautious ones,
 * never less (`../docs/rules.md` — signals may raise `approve → step_up` and can
 * never approve or lower a decline). Flipping it mid-demo is how that gets
 * shown rather than asserted.
 */
export async function setSoftSignals(enabled: boolean): Promise<boolean> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return enabled

  const response = await fetch(`${API_BASE_URL}/dev/soft-signals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!response.ok) throw new Error(`Failed to toggle soft signals (${response.status})`)
  const body = (await response.json()) as { enabled: boolean }
  return body.enabled
}
