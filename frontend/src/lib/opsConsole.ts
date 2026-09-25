import type { Health } from '../api/ops'
import type { CurrentRun } from '../api/operator'
import type { Decision, ScenarioSummary } from '../api/types'

/**
 * Pure logic behind the operator console (`/ops`, `src/ops/`). Nothing here
 * decides a purchase: it names what the backend reported, in the console's
 * words. Kept apart from the components so `npm test` covers it.
 */

// Current run (D7) ----------------------------------------------------------------------

export interface ConsoleRun {
  kind: 'live' | 'replay'
  // Identity: changes exactly when a new run starts, from any source.
  key: string
  // The platform's run id (live runs only).
  runId: string | null
  // The run_id its C6 decisions carry; null from a backend that does not say.
  ledgerRunId: string | null
  scenarioId: string
  cardId: string
  customerId: string | null
  customerName: string | null
  // Real clock.
  startedAt: string | null
  state: 'starting' | 'running' | 'done' | 'error'
  delivered: number
  total: number
  decided: number | null
  // Live runs only; a replay's waiting rows are counted from the stream.
  pendingHuman: number | null
  lastError: string | null
}

/** D7's answer, live or replay, as one shape. */
export function consoleRun(current: CurrentRun | null): ConsoleRun | null {
  if (!current) return null
  const { run } = current
  const common = {
    scenarioId: run.scenario_id,
    cardId: run.card_id,
    customerId: run.customer_id ?? null,
    customerName: run.customer_name ?? null,
    startedAt: run.started_at ?? null,
    ledgerRunId: run.ledger_run_id ?? null,
    delivered: run.delivered,
    total: run.total,
  }
  if (current.kind === 'live') {
    const live = current.run
    return {
      ...common,
      kind: 'live',
      key: live.ledger_run_id ?? `live:${live.run_id}`,
      runId: live.run_id,
      state: live.state,
      decided: live.decided,
      pendingHuman: live.pending_human,
      lastError: live.last_error,
    }
  }
  const replay = current.run
  return {
    ...common,
    kind: 'replay',
    key: replay.ledger_run_id ?? `replay:${replay.scenario_id}:${replay.card_id}:${replay.started_at ?? ''}`,
    runId: null,
    state: replay.running ? 'running' : 'done',
    decided: replay.decided ?? null,
    pendingHuman: null,
    lastError: null,
  }
}

/**
 * The run's own decisions out of the customer's C6 list, order kept (newest
 * first). A backend that names no ledger run id falls back to the card's newest
 * run by `run_started_at`.
 */
export function runDecisions(decisions: Decision[], run: Pick<ConsoleRun, 'ledgerRunId' | 'cardId'>): Decision[] {
  if (run.ledgerRunId) return decisions.filter((d) => d.run_id === run.ledgerRunId)
  const onCard = decisions.filter((d) => d.card_id === run.cardId && d.run_id)
  let newest: Decision | null = null
  for (const d of onCard) {
    if (!newest || (d.run_started_at ?? '') > (newest.run_started_at ?? '')) newest = d
  }
  return newest ? onCard.filter((d) => d.run_id === newest.run_id) : []
}

/**
 * The stream's order: newest delivered first. C6 sorts by simulated time, so a
 * replay's next purchase can belong mid-list; the console puts each decision it
 * has not shown before on top instead, in C6's order among those arriving
 * together. The first read therefore keeps C6's order, and after that the stream
 * fills in delivery order. Ids no longer sent keep their place.
 */
export function arrivalOrder(previous: readonly string[], incoming: readonly Decision[]): string[] {
  const known = new Set(previous)
  const fresh = incoming.map((d) => d.authorization_id).filter((id) => !known.has(id))
  return fresh.length === 0 ? [...previous] : [...fresh, ...previous]
}

export function isWaiting(d: Decision): boolean {
  return d.status === 'pending_human'
}

// Outcome badge -------------------------------------------------------------------------

export type BadgeTone = 'approved' | 'stopped' | 'asked' | 'muted'

export interface OutcomeBadge {
  label: 'Approved' | 'Stopped' | 'Waiting' | 'Expired' | 'Answered'
  tone: BadgeTone
  // Answered only: what the customer said.
  answer: 'approved' | 'declined' | null
  // Spelled out for screen readers; the badge itself also carries an icon.
  description: string
}

/** The five words the stream uses. A step-up stays a step-up once resolved. */
export function outcomeBadge(d: Decision): OutcomeBadge {
  if (d.decision === 'approved') {
    return { label: 'Approved', tone: 'approved', answer: null, description: 'Approved by the rules' }
  }
  if (d.decision === 'stopped') {
    return { label: 'Stopped', tone: 'stopped', answer: null, description: 'Stopped by the rules' }
  }
  switch (d.uncertain_outcome) {
    case 'approved':
      return { label: 'Answered', tone: 'approved', answer: 'approved', description: 'Answered: the customer approved' }
    case 'declined':
      return { label: 'Answered', tone: 'stopped', answer: 'declined', description: 'Answered: the customer declined' }
    case 'expired':
      return { label: 'Expired', tone: 'muted', answer: null, description: 'Expired: no answer in time, nothing approved' }
    default:
      return { label: 'Waiting', tone: 'asked', answer: null, description: 'Waiting for the customer' }
  }
}

// Formatting ----------------------------------------------------------------------------

/** "0:07", "12:40", "1:02:05". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

/** Engine wall time: "0.8 ms", "4.2 ms", "37 ms". */
export function formatLatency(ms: number): string {
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`
}

/** Real-clock start in the viewer's own time: "14:02:11". */
export function formatClock(iso: string): string {
  const date = new Date(iso)
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
}

export function signInLine(name: string, customerId: string, cardId: string): string {
  return `Sign in as ${name} (${customerId}, card ${cardId})`
}

// Health chips --------------------------------------------------------------------------

export type ChipTone = 'ok' | 'warn' | 'bad' | 'neutral'

export interface HealthChip {
  id: string
  label: string
  tone: ChipTone
}

const WORKER_TONE: Record<string, ChipTone> = {
  polling: 'ok',
  standby: 'warn',
  starting: 'neutral',
  degraded: 'bad',
  stopped: 'bad',
}

/** `/healthz` as the header's chips, in a fixed order. */
export function healthChips(health: Health): HealthChip[] {
  const worker = health.worker
  const workerState = worker?.configured ? (worker.state ?? 'unknown') : 'off'
  const provider = health.provider
  const providerName = provider?.configured && provider.name && provider.name !== 'null' ? provider.name : 'none'
  const signals = health.signals
  const modelLoaded = signals?.model_loaded ?? health.model_loaded ?? false
  const db = health.database
  const stubbed = health.engine?.stubbed ?? []

  return [
    { id: 'worker', label: `worker: ${workerState}`, tone: worker?.configured ? (WORKER_TONE[workerState] ?? 'neutral') : 'neutral' },
    { id: 'provider', label: `provider: ${providerName}`, tone: providerName === 'none' ? 'neutral' : 'ok' },
    { id: 'signals', label: `signals: ${signals?.backend ?? 'unknown'}`, tone: signals?.backend && signals.backend !== 'off' ? 'ok' : 'neutral' },
    {
      id: 'model',
      label: modelLoaded ? 'model: loaded' : signals?.model_loading ? 'model: loading' : 'model: not loaded',
      tone: modelLoaded ? 'ok' : signals?.model_loading ? 'warn' : 'neutral',
    },
    db?.ok === false
      ? { id: 'db', label: 'DB: no answer', tone: 'bad' }
      : { id: 'db', label: db?.round_trip_ms == null ? 'DB: -' : `DB ${db.round_trip_ms} ms`, tone: 'ok' },
    stubbed.length === 0
      ? { id: 'engine', label: 'engine: nothing stubbed', tone: 'ok' }
      : { id: 'engine', label: `engine: stubbed ${stubbed.join(', ')}`, tone: 'bad' },
  ]
}

/**
 * Why "Judging run (live)" cannot start, or `null` when it can: D3 hands the run
 * to the Viseca worker, so it needs `/healthz` to show that worker configured and
 * polling. A health read that has not arrived, failed, or is mock mode shows
 * nothing about the worker, so the button waits for it rather than guessing.
 */
export function judgingRunBlocked(health: Health | null | undefined, failed = false): string | null {
  if (failed) return 'Judging run needs the worker polling: /healthz did not answer.'
  if (health === undefined) return 'Judging run needs the worker polling: reading /healthz…'
  if (health === null) return 'Judging run needs the worker: mock mode has none.'
  const worker = health.worker
  if (!worker?.configured) return 'Judging run needs the worker: it is off on this server (not configured).'
  const state = worker.state ?? 'unknown'
  if (state === 'polling') return null
  const error = worker.last_error ? ` Last error: ${worker.last_error}` : ''
  return `Judging run needs the worker polling: it is ${state}.${error}`
}

// Scenario picker (D9) ------------------------------------------------------------------

export interface ScenarioGroup {
  key: string
  label: string
  scenarios: ScenarioSummary[]
}

/** D9 sends one customer's scenarios together; this keeps that order. */
export function groupScenarios(scenarios: ScenarioSummary[]): ScenarioGroup[] {
  const groups: ScenarioGroup[] = []
  for (const s of scenarios) {
    const key = s.customer_id ?? ''
    const last = groups[groups.length - 1]
    if (last && last.key === key) {
      last.scenarios.push(s)
      continue
    }
    const label = s.customer_id ? `${s.customer_name ?? s.customer_id} (${s.customer_id})` : 'No card named yet'
    groups.push({ key, label, scenarios: [s] })
  }
  return groups
}

/** "SCEN0104 · Cross-border purchase · 10". */
export function scenarioOptionLabel(s: ScenarioSummary): string {
  return `${s.scenario_id} · ${s.name} · ${s.event_count}`
}

// Passport and receipts ------------------------------------------------------------------
//
// The passport endpoints (docs/passport.md, api-contract §1.3) are read defensively:
// each reader answers null rather than guess, so a console facing a backend without
// them shows "Passport —", never a wrong count.

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function count(value: unknown): number | null {
  if (Array.isArray(value)) return value.length
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export interface PassportSummary {
  version: string
  checks: number
  devices: number
}

/** "Passport v<n> · <k> checks · <d> devices" needs all three; else null. */
export function passportSummary(raw: unknown): PassportSummary | null {
  const top = record(raw)
  if (!top) return null
  const doc = record(top.passport) ?? top
  const version = doc.version ?? doc.passport_version
  const checks = count(doc.checks) ?? count(record(doc.policy)?.checks)
  const devices = count(doc.devices)
  if ((typeof version !== 'number' && typeof version !== 'string') || checks === null || devices === null) return null
  return { version: String(version), checks, devices }
}

export interface Verification {
  verified: boolean
  keyId: string | null
}

/** `/api/verify`'s answer: verified only when it says so in as many words. */
export function readVerification(raw: unknown): Verification | null {
  const body = record(raw)
  if (!body) return null
  const flag = body.verified ?? body.valid ?? body.ok
  if (typeof flag !== 'boolean') return null
  const key = body.key_id ?? body.kid ?? record(body.key)?.id
  return { verified: flag, keyId: typeof key === 'string' ? key : null }
}

/** The decision's signed receipt, when the backend attaches one inline. */
export function receiptOf(d: Decision): unknown | null {
  return (d as unknown as Record<string, unknown>).receipt ?? null
}

/** The decision has a signed receipt: named by `receipt_id` (P4), or attached inline. */
export function hasReceipt(d: Decision): boolean {
  return Boolean(d.receipt_id) || receiptOf(d) !== null
}

/**
 * What the agent was told would get the purchase approved (`would_approve_if`,
 * on the decision or its receipt), as sentences. Absent: an empty list, and the
 * console leaves the section out.
 */
export function wouldApproveIf(d: Decision): string[] {
  // The backend's bounds are structured (`{field, operator, value}`, `{requires}`, …);
  // the customer's own words for them are the decision's counterfactual.
  if (Array.isArray(d.would_approve_if) && d.would_approve_if.length > 0 && d.counterfactual?.trim()) {
    return [d.counterfactual.trim()]
  }
  const own = (d as unknown as Record<string, unknown>).would_approve_if
  const value = own ?? record(record(receiptOf(d))?.payload)?.would_approve_if ?? record(receiptOf(d))?.would_approve_if
  const items = Array.isArray(value) ? value : value == null ? [] : [value]
  return items
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      const r = record(item)
      const text = r?.text ?? r?.message ?? r?.description
      return typeof text === 'string' ? text.trim() : ''
    })
    .filter((line) => line.length > 0)
}
