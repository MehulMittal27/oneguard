import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Decision, ScenarioSummary } from '../src/api/types.ts'
import {
  arrivalOrder,
  consoleRun,
  exportFileName,
  filterCounts,
  filterDecisions,
  flattenHealth,
  formatElapsed,
  formatLatency,
  groupScenarios,
  healthChips,
  judgingRunBlocked,
  outcomeBadge,
  hasReceipt,
  passportSummary,
  readVerification,
  runDecisions,
  scenarioOptionLabel,
  signInLine,
  wouldApproveIf,
} from '../src/lib/opsConsole.ts'

function decision(fields: Partial<Decision>): Decision {
  return {
    authorization_id: 'a',
    card_id: 'CA1',
    decision: 'approved',
    uncertain_outcome: null,
    status: 'final',
    ...fields,
  } as Decision
}

test('a live run and a replay read as one shape, keyed by the run their decisions carry', () => {
  const live = consoleRun({
    kind: 'live',
    run: {
      run_id: 'RUN1',
      scenario_id: 'S1',
      card_id: 'CA1',
      mandate_id: 'm',
      state: 'running',
      delivered: 3,
      decided: 3,
      pending_human: 1,
      total: 10,
      worker_ok: true,
      last_error: null,
      customer_id: 'CU1',
      customer_name: 'Hannah Chen',
      ledger_run_id: 'live-RUN1',
      started_at: '2026-09-25T10:00:00Z',
    },
  })
  assert.equal(live?.key, 'live-RUN1')
  assert.equal(live?.pendingHuman, 1)
  assert.equal(live?.customerName, 'Hannah Chen')

  const replay = consoleRun({
    kind: 'replay',
    run: { scenario_id: 'S2', card_id: 'CA2', delivered: 11, total: 11, running: false, next_at: null, ledger_run_id: 'replay-x', decided: 11 },
  })
  assert.deepEqual(
    [replay?.kind, replay?.key, replay?.state, replay?.decided, replay?.pendingHuman],
    ['replay', 'replay-x', 'done', 11, null],
  )
  assert.equal(consoleRun(null), null)
})

test('the stream keeps only the current run, newest first as C6 sent it', () => {
  const rows = [
    decision({ authorization_id: 'b2', run_id: 'r2' }),
    decision({ authorization_id: 'a1', run_id: 'r1' }),
    decision({ authorization_id: 'b1', run_id: 'r2' }),
  ]
  const run = consoleRun({
    kind: 'replay',
    run: { scenario_id: 'S', card_id: 'CA1', delivered: 2, total: 2, running: false, next_at: null, ledger_run_id: 'r2' },
  })!
  assert.deepEqual(runDecisions(rows, run).map((d) => d.authorization_id), ['b2', 'b1'])

  // an older backend names no ledger run: the card's newest run by its start
  const older = { ...run, ledgerRunId: null }
  const dated = [
    decision({ authorization_id: 'x', run_id: 'r1', run_started_at: '2026-09-25T09:00:00Z' }),
    decision({ authorization_id: 'y', run_id: 'r2', run_started_at: '2026-09-25T10:00:00Z' }),
    decision({ authorization_id: 'z', card_id: 'CA9', run_id: 'r3', run_started_at: '2026-09-25T11:00:00Z' }),
  ]
  assert.deepEqual(runDecisions(dated, older).map((d) => d.authorization_id), ['y'])
})

test('five outcome words; a resolved step-up reads as answered, never as approved by the rules', () => {
  assert.equal(outcomeBadge(decision({ decision: 'approved' })).label, 'Approved')
  assert.equal(outcomeBadge(decision({ decision: 'stopped' })).label, 'Stopped')
  const waiting = outcomeBadge(decision({ decision: 'uncertain', uncertain_outcome: 'pending', status: 'pending_human' }))
  assert.deepEqual([waiting.label, waiting.tone], ['Waiting', 'asked'])
  assert.equal(outcomeBadge(decision({ decision: 'uncertain', uncertain_outcome: 'expired' })).label, 'Expired')
  const yes = outcomeBadge(decision({ decision: 'uncertain', uncertain_outcome: 'approved' }))
  assert.deepEqual([yes.label, yes.answer], ['Answered', 'approved'])
  const no = outcomeBadge(decision({ decision: 'uncertain', uncertain_outcome: 'declined' }))
  assert.deepEqual([no.label, no.answer, no.tone], ['Answered', 'declined', 'stopped'])
})

test('elapsed time and latency read at a glance', () => {
  assert.equal(formatElapsed(7_400), '0:07')
  assert.equal(formatElapsed(760_000), '12:40')
  assert.equal(formatElapsed(3_725_000), '1:02:05')
  assert.equal(formatElapsed(-5), '0:00')
  assert.equal(formatLatency(4.237), '4.2 ms')
  assert.equal(formatLatency(37.4), '37 ms')
  assert.equal(signInLine('Oliver Graf', 'CU0019', 'CA0039'), 'Sign in as Oliver Graf (CU0019, card CA0039)')
})

test('health chips say what runs; a stubbed engine is red', () => {
  const chips = healthChips({
    worker: { configured: false },
    provider: { name: 'null', configured: false },
    signals: { backend: 'keywords', model_loaded: false, model_loading: false },
    database: { ok: true, round_trip_ms: 0.4 },
    engine: { stubbed: [] },
  })
  assert.deepEqual(
    chips.map((c) => [c.label, c.tone]),
    [
      ['worker: off', 'neutral'],
      ['provider: none', 'neutral'],
      ['signals: keywords', 'ok'],
      ['model: not loaded', 'neutral'],
      ['DB 0.4 ms', 'ok'],
      ['engine: nothing stubbed', 'ok'],
    ],
  )
  const polling = healthChips({ worker: { configured: true, state: 'polling' }, database: { ok: false }, engine: { stubbed: ['decide'] } })
  assert.deepEqual(polling[0], { id: 'worker', label: 'worker: polling', tone: 'ok' })
  assert.deepEqual(polling[4], { id: 'db', label: 'DB: no answer', tone: 'bad' })
  assert.deepEqual(polling[5], { id: 'engine', label: 'engine: stubbed decide', tone: 'bad' })
  assert.equal(healthChips({ worker: { configured: true, state: 'standby' } })[0].tone, 'warn')
})

test('scenarios group by customer in the order D9 sends them', () => {
  const s = (id: string, customer: string | null): ScenarioSummary => ({
    scenario_id: id,
    name: 'Name',
    event_count: 3,
    instruction: 'Buy it.',
    customer_id: customer,
    customer_name: customer ? `Name ${customer}` : null,
    card_id: customer ? 'CA' : null,
  })
  const groups = groupScenarios([s('S1', 'CU1'), s('S2', 'CU1'), s('S3', 'CU2'), s('S9', null)])
  assert.deepEqual(
    groups.map((g) => [g.label, g.scenarios.map((x) => x.scenario_id)]),
    [
      ['Name CU1 (CU1)', ['S1', 'S2']],
      ['Name CU2 (CU2)', ['S3']],
      ['No card named yet', ['S9']],
    ],
  )
  assert.equal(scenarioOptionLabel({ ...s('SCEN0104', 'CU1'), name: 'Cross-border purchase', event_count: 10 }), 'SCEN0104 · Cross-border purchase · 10')
})

test('a passport summary needs version, checks and devices; anything less is no summary', () => {
  assert.deepEqual(passportSummary({ version: 3, checks: [{}, {}, {}], devices: [{}, {}] }), { version: '3', checks: 3, devices: 2 })
  assert.deepEqual(passportSummary({ passport: { version: 1, policy: { checks: [{}] }, devices: 1 } }), {
    version: '1',
    checks: 1,
    devices: 1,
  })
  assert.equal(passportSummary({ version: 3, checks: [] }), null)
  assert.equal(passportSummary(null), null)
  assert.equal(passportSummary('v3'), null)
})

test('verified only when the verifier says so in as many words', () => {
  assert.deepEqual(readVerification({ verified: true, key_id: 'k1' }), { verified: true, keyId: 'k1' })
  assert.deepEqual(readVerification({ valid: false }), { verified: false, keyId: null })
  assert.equal(readVerification({ verified: 'yes' }), null)
  assert.equal(readVerification(null), null)
})

test('what the agent was told comes from the decision or its receipt, as sentences', () => {
  assert.deepEqual(wouldApproveIf(decision({})), [])
  assert.deepEqual(
    wouldApproveIf({ ...decision({}), would_approve_if: ['Total at or below CHF 400.00', ' '] } as unknown as Decision),
    ['Total at or below CHF 400.00'],
  )
  assert.deepEqual(
    wouldApproveIf({ ...decision({}), receipt: { payload: { would_approve_if: [{ text: 'A shop you know' }] } } } as unknown as Decision),
    ['A shop you know'],
  )
})

test('the stream fills in delivery order: what arrives goes on top, whatever its simulated time', () => {
  const d = (id: string) => decision({ authorization_id: id })
  // first read: C6's own order
  let order = arrivalOrder([], [d('late'), d('early')])
  assert.deepEqual(order, ['late', 'early'])
  // the next purchase is simulated earlier than both, but it is the newest delivered
  order = arrivalOrder(order, [d('late'), d('earliest'), d('early')])
  assert.deepEqual(order, ['earliest', 'late', 'early'])
  // two at once keep C6's order between them; nothing moves on a repeat read
  order = arrivalOrder(order, [d('b'), d('late'), d('a'), d('earliest'), d('early')])
  assert.deepEqual(order, ['b', 'a', 'earliest', 'late', 'early'])
  assert.deepEqual(arrivalOrder(order, [d('a')]), order)
})

test('a judging run waits for /healthz to show the worker polling, and says why not', () => {
  assert.equal(judgingRunBlocked({ worker: { configured: true, state: 'polling', ok: true } }), null)
  assert.equal(
    judgingRunBlocked({ worker: { configured: false } }),
    'Judging run needs the worker: it is off on this server (not configured).',
  )
  assert.equal(judgingRunBlocked({}), 'Judging run needs the worker: it is off on this server (not configured).')
  assert.equal(
    judgingRunBlocked({ worker: { configured: true, state: 'standby' } }),
    'Judging run needs the worker polling: it is standby.',
  )
  assert.equal(
    judgingRunBlocked({ worker: { configured: true, state: 'degraded', last_error: 'events: 401' } }),
    'Judging run needs the worker polling: it is degraded. Last error: events: 401',
  )
  assert.equal(judgingRunBlocked({ worker: { configured: true } }), 'Judging run needs the worker polling: it is unknown.')
  assert.equal(judgingRunBlocked(undefined), 'Judging run needs the worker polling: reading /healthz…')
  assert.equal(judgingRunBlocked(null), 'Judging run needs the worker: mock mode has none.')
  assert.equal(
    judgingRunBlocked({ worker: { configured: true, state: 'polling' } }, true),
    'Judging run needs the worker polling: /healthz did not answer.',
  )
})

test('the passport line reads the real signed passport: version, checks and devices', () => {
  const fixture = JSON.parse(readFileSync(new URL('../src/mocks/fixtures/passport.json', import.meta.url), 'utf8'))
  const doc = fixture.passport.document
  assert.deepEqual(passportSummary(doc), { version: String(doc.version), checks: doc.checks.length, devices: doc.devices.length })
  assert.deepEqual(readVerification({ valid: true, key_id: fixture.passport.key_id, reason: 'Signed by OneGuard.' }), {
    verified: true,
    keyId: fixture.passport.key_id,
  })
})

test('a decision naming a receipt_id has a receipt to verify; one without has none', () => {
  assert.equal(hasReceipt(decision({})), false)
  assert.equal(hasReceipt({ ...decision({}), receipt_id: 'rc_1' } as Decision), true)
})

test('what the agent was told from the backend bounds is said in the counterfactual\'s words', () => {
  const told = {
    ...decision({}),
    would_approve_if: [{ field: 'authorization.billing_amount_chf', operator: '<=', value: 400 }],
    counterfactual: 'Would approve at CHF 400.00 or less.',
  } as Decision
  assert.deepEqual(wouldApproveIf(told), ['Would approve at CHF 400.00 or less.'])
  assert.deepEqual(wouldApproveIf({ ...told, would_approve_if: null } as Decision), [])
})

test('the decision log filters by outcome and searches shops, accents and case aside', () => {
  const row = (id: string, name: string, fields: Partial<Decision>) =>
    decision({ authorization_id: id, merchant: { merchant_id: id, name }, ...fields })
  const rows = [
    row('a', 'PixelHarbor', { decision: 'approved' }),
    row('b', 'Café Zürich', { decision: 'stopped' }),
    row('c', 'PixelHarbour', { decision: 'uncertain', uncertain_outcome: 'pending', status: 'pending_human' }),
    row('d', 'HarborByte', { decision: 'uncertain', uncertain_outcome: 'declined' }),
    row('e', 'HarborByte', { decision: 'uncertain', uncertain_outcome: 'expired' }),
  ]
  const ids = (list: Decision[]) => list.map((d) => d.authorization_id)
  assert.deepEqual(ids(filterDecisions(rows, 'all', '')), ['a', 'b', 'c', 'd', 'e'])
  assert.deepEqual(ids(filterDecisions(rows, 'approved', '')), ['a'])
  assert.deepEqual(ids(filterDecisions(rows, 'stopped', '')), ['b'])
  assert.deepEqual(ids(filterDecisions(rows, 'waiting', '')), ['c'])
  assert.deepEqual(ids(filterDecisions(rows, 'answered', '')), ['d']) // expired is nobody's answer
  assert.deepEqual(ids(filterDecisions(rows, 'all', ' pixelharb ')), ['a', 'c'])
  assert.deepEqual(ids(filterDecisions(rows, 'all', 'cafe zurich')), ['b'])
  assert.deepEqual(ids(filterDecisions(rows, 'stopped', 'pixel')), [])
  assert.deepEqual(filterCounts(rows), { all: 5, approved: 1, stopped: 1, waiting: 1, answered: 1 })
})

test('an export is named after the run, safely', () => {
  assert.equal(exportFileName('SCEN0004', 'replay'), 'oneguard-SCEN0004-replay-decisions.json')
  assert.equal(exportFileName(null, null), 'oneguard-decisions.json')
  assert.equal(exportFileName('../x y', 'live'), 'oneguard-x-y-live-decisions.json')
})

test('the health table shows every key of /healthz, nested keys joined with dots', () => {
  assert.deepEqual(
    flattenHealth({ status: 'ok', worker: { state: 'polling', last_error: null }, engine: { stubbed: [] }, runs: ['a', 'b'] }),
    [
      { key: 'status', value: 'ok' },
      { key: 'worker.state', value: 'polling' },
      { key: 'worker.last_error', value: 'null' },
      { key: 'engine.stubbed', value: 'none' },
      { key: 'runs', value: 'a, b' },
    ],
  )
  assert.deepEqual(flattenHealth(null), [])
})
