import type { CatalogueScenario, ReplayStatus, LiveRun, ScenarioSummary } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * What the operator console (`/ops`) reads and starts beyond `operator.ts`:
 * `/healthz`, D8 and D9's scenario lists, D4 (a stored run), D2 (replay) and D3
 * (a judging run). Operator
 * only, like everything in `operator.ts`: no customer screen imports this.
 *
 * With mocks on nothing runs, so health is `null` ("mock mode"), the list is
 * empty and nothing can start: a console that showed invented counters would put
 * numbers on screen that stand for nothing.
 */

/** `/healthz`, as much of it as the console shows. Every part is optional: an
 * older backend or a worker that never started leaves parts out. */
export interface Health {
  status?: 'ok' | 'degraded'
  worker?: {
    configured?: boolean
    state?: string
    ok?: boolean
    last_error?: string | null
    last_poll_at?: string | null
    events_cursor?: number | string | null
  }
  events_cursor?: number | string | null
  // The machine the server runs on, when the host says (Fly: region, memory, CPUs).
  machine?: { region?: string | null; memory_mb?: number | null; cpus?: number | null; machine_id?: string | null }
  provider?: { name?: string; configured?: boolean }
  signals?: { backend?: string; configured?: string; enabled?: boolean; model_loading?: boolean; model_loaded?: boolean }
  model_loaded?: boolean
  database?: { engine?: string; ok?: boolean; round_trip_ms?: number | null }
  engine?: { stubbed?: string[] }
  runs_allowed?: boolean
}

/**
 * A refusal the backend explained (`{ error: { code, message } }`, contract
 * §3.8). The console shows `message` verbatim: the operator needs the server's
 * own words ("A run is already open…"), not a paraphrase.
 */
export class ApiRefusal extends Error {
  readonly status: number
  readonly code: string | null

  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function refusal(response: Response, fallback: string): Promise<ApiRefusal> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } }
    if (body.error?.message) return new ApiRefusal(response.status, body.error.code ?? null, body.error.message)
  } catch {
    // Not JSON (a proxy's HTML error page): fall through to our own words.
  }
  return new ApiRefusal(response.status, null, `${fallback} (${response.status})`)
}

/**
 * `/healthz`. A 503 still carries the JSON body (the database did not answer),
 * so it is read, not thrown; only no answer at all throws.
 */
export async function getHealth(): Promise<Health | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return null

  const response = await fetch('/healthz', { cache: 'no-store' })
  if (!response.ok && response.status !== 503) throw new Error(`Health check failed (${response.status})`)
  return (await response.json()) as Health
}

/** D9: every scenario in the store, grouped by customer in the order sent. */
export async function getScenarios(): Promise<ScenarioSummary[]> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return []

  const response = await fetch(`${API_BASE_URL}/scenarios`)
  if (!response.ok) throw await refusal(response, 'Could not read the scenarios')
  return ((await response.json()) as { scenarios: ScenarioSummary[] }).scenarios
}

/**
 * D8: the store's catalogue with what the platform serves now. The backend first
 * re-reads the platform's bootstrap, so this is read once per run, not polled.
 */
export async function getCatalogue(): Promise<CatalogueScenario[]> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return []

  const response = await fetch(`${API_BASE_URL}/dev/scenarios`)
  if (!response.ok) throw await refusal(response, 'Could not read the served scenarios')
  return ((await response.json()) as { scenarios: CatalogueScenario[] }).scenarios
}

/** D4: one live run by the platform's run id, as the worker or the store last saw it. */
export async function getLiveRun(runId: string): Promise<LiveRun> {
  const response = await fetch(`${API_BASE_URL}/dev/runs/${encodeURIComponent(runId)}`)
  if (!response.ok) throw await refusal(response, `Could not read run ${runId}`)
  return (await response.json()) as LiveRun
}

/** D2: replay a scenario of the local data pack, `speedMs` apart. */
export async function restartReplay(scenarioId: string, cardId: string, speedMs: number): Promise<ReplayStatus> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    throw new ApiRefusal(0, null, 'Mock mode: nothing can be replayed.')
  }

  const response = await fetch(`${API_BASE_URL}/dev/replay/restart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_id: scenarioId, card_id: cardId, speed_ms: speedMs }),
  })
  if (!response.ok) throw await refusal(response, 'The replay did not start')
  return (await response.json()) as ReplayStatus
}

/**
 * D3: a judging run against the payment platform. Never forced: a 409
 * (`run_active`, `runs_disabled`) comes back as an `ApiRefusal` for the console
 * to show as is.
 */
export async function startJudgingRun(scenarioId: string, cardId: string): Promise<LiveRun> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    throw new ApiRefusal(0, null, 'Mock mode: no judging run can start.')
  }

  const response = await fetch(`${API_BASE_URL}/dev/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_id: scenarioId, card_id: cardId }),
  })
  if (!response.ok) throw await refusal(response, 'The judging run did not start')
  return (await response.json()) as LiveRun
}
