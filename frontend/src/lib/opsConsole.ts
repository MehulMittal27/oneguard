import type { Health } from '../api/ops'
import type { CurrentRun } from '../api/operator'
import type { Decision, LiveRun, PlatformMandate, ReplayStatus, ScenarioSummary } from '../api/types'

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
  // Replays only: which policy decides the run, as the run header words it;
  // null for a live run (always the card's policy) or an older backend.
  policy: string | null
  // A replay from record: the live run whose stored events it replays (the
  // platform's run id) and when that run started. Null for a pack replay or a live run.
  fromRecord: { runId: string; startedAt: string | null } | null
  // Live runs only: D3 registered the policy at the platform again before the run
  // (its mandate there was superseded or missing), as the run header words it,
  // with the detail for its tooltip. Null when D3 did not.
  platformMandate: { label: string; detail: string } | null
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
      policy: null,
      fromRecord: null,
      platformMandate: platformMandateNote(live.platform_mandate),
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
    policy: replayPolicy(replay.policy_source),
    fromRecord:
      replay.source === 'record' && replay.record_run_id
        ? { runId: replay.record_run_id, startedAt: replay.record_started_at ?? null }
        : null,
    platformMandate: null,
  }
}

/**
 * "platform mandate re-registered" when D3 found the policy's mandate at the
 * platform superseded (or missing, revoked there) and registered the same policy
 * again; the detail names both platform mandates. Null otherwise: the local
 * policy is what decides, so an active platform mandate needs no word.
 */
export function platformMandateNote(
  platform: PlatformMandate | undefined,
): { label: string; detail: string } | null {
  if (!platform?.reregistered) return null
  const before = platform.previous_viseca_mandate_id ?? 'The previous mandate'
  const status = platform.status_before ?? 'not active'
  return {
    label: 'platform mandate re-registered',
    detail: `${before} was ${status} at the platform; the same policy now runs as ${platform.viseca_mandate_id}.`,
  }
}

/** The run header's words for where a replay's policy came from (D2). */
export function replayPolicy(source: ReplayStatus['policy_source']): string | null {
  if (source === 'scenario') return 'policy compiled from the scenario'
  if (source === 'revoked') return 'policy revoked: every purchase declines'
  if (source === 'card') return "the card's active policy"
  return null
}

/** What the run is, in the console's words. */
export function runTitle(run: Pick<ConsoleRun, 'kind' | 'fromRecord'>): string {
  if (run.kind === 'live') return 'Judging run'
  return run.fromRecord ? 'Replay from record' : 'Replay'
}

/**
 * The run D7 names, on its own line beside the selected scenario's sign-in:
 * "Last run: Replay from record · SCEN0101 · Omar Chen (CU1217, card CA1331) · running".
 */
export function lastRunLine(run: ConsoleRun): string {
  const who = run.customerId
    ? `${run.customerName ?? run.customerId} (${run.customerId}, card ${run.cardId})`
    : `card ${run.cardId}`
  return `Last run: ${runTitle(run)} · ${run.scenarioId} · ${who} · ${run.state}`
}

/**
 * In progress: starting or running, or with step-ups still waiting for the
 * customer (`waiting`, counted from the stream; D7's `pending_human` for a live run).
 */
export function runInProgress(run: Pick<ConsoleRun, 'state' | 'pendingHuman'>, waiting: number): boolean {
  return run.state === 'starting' || run.state === 'running' || waiting > 0 || (run.pendingHuman ?? 0) > 0
}

export interface RunPanel {
  run: ConsoleRun
  // Not in progress: shown only because its scenario is selected.
  finished: boolean
}

/**
 * The run the run panel shows: D7's run while it is in progress; once it is
 * not, only while its scenario is selected (D7 names the newest run, so it is
 * that scenario's latest), labelled finished. Otherwise null: "No run in
 * progress", with the last run still named on its own line.
 */
export function runPanel(run: ConsoleRun | null, waiting: number, selectedScenarioId: string | null): RunPanel | null {
  if (!run) return null
  if (runInProgress(run, waiting)) return { run, finished: false }
  return run.scenarioId === selectedScenarioId ? { run, finished: true } : null
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

/** Whom to sign in as for the selected scenario: its customer, whatever ran last. */
export function scenarioSignIn(scenario: ScenarioSummary | null): string | null {
  if (!scenario?.customer_id || !scenario.card_id) return null
  return signInLine(scenario.customer_name ?? scenario.customer_id, scenario.customer_id, scenario.card_id)
}

export interface PhoneCustomer {
  customerId: string | null
  customerName: string | null
}

/**
 * Whom the embedded phone is signed in as: the customer of the run the panel
 * shows (`runPanel`: a run in progress, or a finished one while its scenario is
 * selected), else the selected scenario's customer, so the phone never keeps a
 * finished run's customer under another scenario's "Sign in as" line.
 */
export function phoneCustomer(
  shown: Pick<ConsoleRun, 'customerId' | 'customerName'> | null | undefined,
  selected: ScenarioSummary | null,
): PhoneCustomer {
  if (shown?.customerId) return { customerId: shown.customerId, customerName: shown.customerName }
  return { customerId: selected?.customer_id ?? null, customerName: selected?.customer_name ?? null }
}

/**
 * The embedded phone's URL (api-contract §6 item 20). The iframe is keyed on it,
 * so another customer is a new iframe and a fresh app: nothing of the previous
 * customer's cards (passport, devices, this device's role) can stay on screen.
 */
export function phoneEmbedSrc(customerId: string | null): string {
  return customerId ? `/?customer=${encodeURIComponent(customerId)}&embed=1` : '/?embed=1'
}

export interface ReplayGuard {
  // Why D2 cannot replay the scenario, or null.
  blocked: string | null
  // What a replay of it is, when not the pack's purchases.
  note: string | null
}

/**
 * D2 for the selected scenario (D9 `replay_source`): the pack's purchases; a
 * served scenario's stored events from its newest live run ("replay from
 * record", run locally through the current engine); or nothing, before any
 * live run of it. An older backend that does not say is not blocked here: D2
 * refuses in its own words.
 */
export function replayGuard(scenario: ScenarioSummary | null): ReplayGuard {
  if (!scenario || scenario.replay_source === undefined || scenario.replay_source === 'pack') {
    return { blocked: null, note: null }
  }
  if (scenario.replay_source === null) {
    return { blocked: 'Not run yet: no stored events to replay.', note: null }
  }
  return {
    blocked: null,
    note: 'Replay from record: the stored events of its latest judging run, through the current engine and policy. Nothing is sent to the platform.',
  }
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

/** "Thu 24 Sep 14:02": a run's real-clock start in the viewer's own time. */
export function formatRunStart(iso: string): string {
  const date = new Date(iso)
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][date.getDay()]
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][date.getMonth()]
  return `${day} ${date.getDate()} ${month} ${formatClock(iso).slice(0, 5)}`
}

/**
 * The platform's ids of the live runs whose decisions C6 lists on a card, newest
 * start first. A live run's ledger id is `live-<platform run id>`
 * (docs/database.md `runs`); D4 is called with the platform's.
 */
export function liveRunIds(decisions: Decision[], cardId: string): string[] {
  const started = new Map<string, string>()
  for (const d of decisions) {
    if (d.card_id !== cardId || !d.run_id?.startsWith('live-')) continue
    const id = d.run_id.slice('live-'.length)
    const at = d.run_started_at ?? ''
    if (id && (started.get(id) ?? '') <= at) started.set(id, at)
  }
  return [...started].sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : 0)).map(([id]) => id)
}

/** The newest live run of the scenario that finished (`state: done`), or null. */
export function completedRun(runs: LiveRun[], scenarioId: string): LiveRun | null {
  let newest: LiveRun | null = null
  for (const r of runs) {
    if (r.scenario_id !== scenarioId || r.state !== 'done') continue
    if (!newest || (r.started_at ?? '') > (newest.started_at ?? '')) newest = r
  }
  return newest
}

export interface JudgingGuard {
  // Why the button is disabled, the most specific reason first; null: it can start.
  blocked: string | null
  // Said beside an enabled button and again in the confirmation; never disables it.
  warning: string | null
}

/**
 * "Judging run (live)" for the selected scenario. D3 only makes sense for a
 * scenario the platform serves now (D8 `served`); the others are replay only,
 * whatever the worker does, so that reason comes first. Then the worker
 * (`judgingRunBlocked`). A catalogue not read yet, or not answering, is not a
 * yes: the button waits for it, and for the runs on record. A served scenario
 * with a finished live run (`record`) stays startable, with a warning that its
 * run is already on record; runs on record that could not be read warn too.
 */
export function judgingRunGuard({
  health,
  healthFailed = false,
  served,
  record,
}: {
  health: Health | null | undefined
  healthFailed?: boolean
  // D8's `served` for the scenario; undefined while reading, null when D8 did not answer.
  served: boolean | null | undefined
  // The scenario's newest finished live run; undefined while reading, 'failed' when C6 or D4 did not answer.
  record: LiveRun | null | undefined | 'failed'
}): JudgingGuard {
  if (served === false) return { blocked: 'Replay only: not served by the sandbox.', warning: null }
  const warning =
    record === 'failed'
      ? 'The runs on record could not be read: this scenario may already have a finished run.'
      : record
        ? `Already on record (run ${record.started_at ? formatRunStart(record.started_at) : record.run_id}).`
        : null
  const worker = judgingRunBlocked(health, healthFailed)
  if (worker) return { blocked: worker, warning }
  if (served === undefined) return { blocked: 'Judging run needs the served scenarios: reading /api/dev/scenarios…', warning }
  if (served === null) return { blocked: 'Judging run needs the served scenarios: /api/dev/scenarios did not answer.', warning }
  if (record === undefined) return { blocked: 'Judging run needs the runs on record: reading them…', warning }
  return { blocked: null, warning }
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

// Decision log: filters, search, export -----------------------------------------------

export type LogFilter = 'all' | 'approved' | 'stopped' | 'waiting' | 'answered'

export const LOG_FILTERS: { id: LogFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'approved', label: 'Approved' },
  { id: 'stopped', label: 'Stopped' },
  { id: 'waiting', label: 'Waiting' },
  { id: 'answered', label: 'Answered' },
]

const FILTER_BADGE: Record<Exclude<LogFilter, 'all'>, OutcomeBadge['label']> = {
  approved: 'Approved',
  stopped: 'Stopped',
  waiting: 'Waiting',
  answered: 'Answered',
}

/**
 * The rows the decision log shows: those whose badge matches the filter (an
 * expired step-up shows only under All), and whose shop name contains the search,
 * case- and accent-insensitive. Order is kept.
 */
export function filterDecisions(rows: Decision[], filter: LogFilter, query: string): Decision[] {
  const fold = (text: string) => text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
  const needle = fold(query.trim())
  return rows.filter(
    (d) =>
      (filter === 'all' || outcomeBadge(d).label === FILTER_BADGE[filter]) &&
      (needle === '' || fold(d.merchant.name).includes(needle)),
  )
}

/** How many rows each filter would show (the filter chips' counts). */
export function filterCounts(rows: Decision[]): Record<LogFilter, number> {
  const counts: Record<LogFilter, number> = { all: rows.length, approved: 0, stopped: 0, waiting: 0, answered: 0 }
  for (const d of rows) {
    const label = outcomeBadge(d).label
    for (const f of LOG_FILTERS) if (f.id !== 'all' && FILTER_BADGE[f.id] === label) counts[f.id] += 1
  }
  return counts
}

/** "oneguard-SCEN0004-replay-decisions.json": the Export JSON file name, safe for any OS. */
export function exportFileName(scenarioId: string | null, kind: string | null): string {
  const part = (text: string | null) => (text ?? '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  return ['oneguard', part(scenarioId), part(kind), 'decisions'].filter(Boolean).join('-') + '.json'
}

// Health -----------------------------------------------------------------------------------

export interface HealthRow {
  key: string
  value: string
}

/**
 * The whole `/healthz` body as rows, nested keys joined with dots
 * ("worker.state"), in the order the server sent them. Lists read "a, b" (empty:
 * "none"), null reads "null": nothing is left out or reworded.
 */
export function flattenHealth(body: unknown, prefix = ''): HealthRow[] {
  const node = record(body)
  if (!node) return prefix ? [{ key: prefix, value: body === null || body === undefined ? 'null' : String(body) }] : []
  return Object.entries(node).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (Array.isArray(value)) return [{ key: path, value: value.length ? value.map(String).join(', ') : 'none' }]
    if (record(value)) return flattenHealth(value, path)
    return [{ key: path, value: value === null || value === undefined ? 'null' : String(value) }]
  })
}
