import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import type { Decision, LiveRun, ScenarioSummary } from '../src/api/types.ts'
import {
  arrivalOrder,
  completedRun,
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
  judgingRunGuard,
  lastRunLine,
  liveRunIds,
  outcomeBadge,
  hasReceipt,
  passportSummary,
  phoneCustomer,
  phoneEmbedSrc,
  platformMandateNote,
  readVerification,
  replayGuard,
  runDecisions,
  runInProgress,
  runPanel,
  runTitle,
  scenarioOptionLabel,
  scenarioSignIn,
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
    [replay?.kind, replay?.key, replay?.state, replay?.decided, replay?.pendingHuman, replay?.policy],
    ['replay', 'replay-x', 'done', 11, null, null],
  )
  assert.equal(live?.policy, null)
  assert.equal(consoleRun(null), null)
})

test('the run header says when D3 registered the policy at the platform again', () => {
  const live = (platform_mandate?: LiveRun['platform_mandate']) =>
    consoleRun({
      kind: 'live',
      run: {
        run_id: 'RUN1', scenario_id: 'S1', card_id: 'CA1', mandate_id: 'md_1', state: 'running',
        delivered: 0, decided: 0, pending_human: 0, total: 3, worker_ok: true, last_error: null, platform_mandate,
      },
    })?.platformMandate
  assert.deepEqual(
    live({ status_before: 'superseded', reregistered: true, viseca_mandate_id: 'TMnew', previous_viseca_mandate_id: 'TMold' }),
    {
      label: 'platform mandate re-registered',
      detail: 'TMold was superseded at the platform; the same policy now runs as TMnew.',
    },
  )
  assert.equal(live({ status_before: 'active', reregistered: false, viseca_mandate_id: 'TMold' }), null)
  assert.equal(live(undefined), null)
  // the run panel shows the same run, evidence and all, in progress or finished
  const rereg = { status_before: 'superseded', reregistered: true, viseca_mandate_id: 'TMnew', previous_viseca_mandate_id: 'TMold' }
  const running = consoleRun({
    kind: 'live',
    run: {
      run_id: 'RUN1', scenario_id: 'S1', card_id: 'CA1', mandate_id: 'md_1', state: 'running',
      delivered: 0, decided: 0, pending_human: 0, total: 3, worker_ok: true, last_error: null, platform_mandate: rereg,
    },
  })
  assert.ok(running)
  assert.equal(runPanel(running, 0, null)?.run.platformMandate?.label, 'platform mandate re-registered')
  const finished = { ...running, state: 'done' as const, pendingHuman: 0 }
  assert.equal(runPanel(finished, 0, 'S1')?.run.platformMandate?.label, 'platform mandate re-registered')
  assert.equal(
    platformMandateNote({ status_before: null, reregistered: true, viseca_mandate_id: 'TMnew' })?.detail,
    'The previous mandate was not active at the platform; the same policy now runs as TMnew.',
  )
})

test('the run header says which policy a replay decides by', () => {
  const replay = (policy_source?: 'card' | 'revoked' | 'scenario') =>
    consoleRun({
      kind: 'replay',
      run: { scenario_id: 'S', card_id: 'CA1', delivered: 0, total: 1, running: true, next_at: null, policy_source },
    })?.policy
  assert.equal(replay('scenario'), 'policy compiled from the scenario')
  assert.equal(replay('card'), "the card's active policy")
  assert.equal(replay('revoked'), 'policy revoked: every purchase declines')
  assert.equal(replay(undefined), null)
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

function liveRun(fields: Partial<LiveRun>): LiveRun {
  return {
    run_id: 'R1',
    scenario_id: 'SCEN0101',
    card_id: 'CA1',
    mandate_id: 'm',
    state: 'done',
    delivered: 10,
    decided: 10,
    pending_human: 0,
    total: 10,
    worker_ok: false,
    last_error: null,
    ...fields,
  }
}

test('the live runs on record are the card’s live-<id> runs in C6, newest first, platform ids', () => {
  const rows = [
    decision({ authorization_id: 'a', run_id: 'live-OLD', run_started_at: '2026-09-20T09:00:00Z' }),
    decision({ authorization_id: 'b', run_id: 'live-NEW', run_started_at: '2026-09-24T12:00:00Z' }),
    decision({ authorization_id: 'c', run_id: 'live-OLD', run_started_at: '2026-09-20T09:00:00Z' }),
    decision({ authorization_id: 'd', run_id: 'replay-abc', run_started_at: '2026-09-25T08:00:00Z' }),
    decision({ authorization_id: 'e', run_id: 'live-OTHER', card_id: 'CA2' }),
    decision({ authorization_id: 'f' }),
  ]
  assert.deepEqual(liveRunIds(rows, 'CA1'), ['NEW', 'OLD'])
  assert.deepEqual(liveRunIds(rows, 'CA9'), [])
})

test('a scenario is on record by its newest finished live run, never one still going or failed', () => {
  const runs = [
    liveRun({ run_id: 'A', started_at: '2026-09-20T09:00:00Z' }),
    liveRun({ run_id: 'B', started_at: '2026-09-24T12:00:00Z' }),
    liveRun({ run_id: 'C', started_at: '2026-09-25T12:00:00Z', state: 'running' }),
    liveRun({ run_id: 'D', started_at: '2026-09-25T13:00:00Z', state: 'error' }),
    liveRun({ run_id: 'E', started_at: '2026-09-26T12:00:00Z', scenario_id: 'SCEN0102' }),
  ]
  assert.equal(completedRun(runs, 'SCEN0101')?.run_id, 'B')
  assert.equal(completedRun(runs, 'SCEN0103'), null)
  assert.equal(completedRun([liveRun({ state: 'running' })], 'SCEN0101'), null)
})

test('the judging button: served only, worker polling, the most specific reason, a record warns', () => {
  const polling = { worker: { configured: true, state: 'polling' } }
  const standby = { worker: { configured: true, state: 'standby' } }
  const done = liveRun({ run_id: 'R9', started_at: '2026-09-24T12:05:00' })

  // A public scenario is replay only, whatever the worker does.
  for (const health of [polling, standby, undefined, null]) {
    assert.deepEqual(judgingRunGuard({ health, served: false, record: done }), {
      blocked: 'Replay only: not served by the sandbox.',
      warning: null,
    })
  }
  // Served and nothing on record: startable.
  assert.deepEqual(judgingRunGuard({ health: polling, served: true, record: null }), { blocked: null, warning: null })
  // Served and on record: startable, with a warning naming the run's start.
  assert.deepEqual(judgingRunGuard({ health: polling, served: true, record: done }), {
    blocked: null,
    warning: 'Already on record (run Thu 24 Sep 12:05).',
  })
  assert.equal(
    judgingRunGuard({ health: polling, served: true, record: liveRun({ run_id: 'R9', started_at: undefined }) }).warning,
    'Already on record (run R9).',
  )
  // The worker rule still holds for a served scenario, and the warning stays beside it.
  assert.deepEqual(judgingRunGuard({ health: standby, served: true, record: done }), {
    blocked: 'Judging run needs the worker polling: it is standby.',
    warning: 'Already on record (run Thu 24 Sep 12:05).',
  })
  assert.equal(
    judgingRunGuard({ health: polling, healthFailed: true, served: true, record: null }).blocked,
    'Judging run needs the worker polling: /healthz did not answer.',
  )
  // Not known is not a yes.
  assert.equal(
    judgingRunGuard({ health: polling, served: undefined, record: undefined }).blocked,
    'Judging run needs the served scenarios: reading /api/dev/scenarios…',
  )
  assert.equal(
    judgingRunGuard({ health: polling, served: null, record: undefined }).blocked,
    'Judging run needs the served scenarios: /api/dev/scenarios did not answer.',
  )
  assert.deepEqual(judgingRunGuard({ health: polling, served: true, record: undefined }), {
    blocked: 'Judging run needs the runs on record: reading them…',
    warning: null,
  })
  // Runs on record that could not be read warn; they do not block.
  assert.deepEqual(judgingRunGuard({ health: polling, served: true, record: 'failed' }), {
    blocked: null,
    warning: 'The runs on record could not be read: this scenario may already have a finished run.',
  })
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

function summary(fields: Partial<ScenarioSummary>): ScenarioSummary {
  return {
    scenario_id: 'SCEN0101',
    name: 'Groceries',
    event_count: 3,
    instruction: 'Buy groceries.',
    customer_id: 'CU1217',
    customer_name: 'Omar Chen',
    card_id: 'CA1331',
    ...fields,
  }
}

test('the sign-in line follows the selected scenario; the last run is its own line', () => {
  assert.equal(scenarioSignIn(summary({})), 'Sign in as Omar Chen (CU1217, card CA1331)')
  assert.equal(scenarioSignIn(summary({ customer_name: null })), 'Sign in as CU1217 (CU1217, card CA1331)')
  assert.equal(scenarioSignIn(summary({ customer_id: null, customer_name: null, card_id: null })), null)
  assert.equal(scenarioSignIn(null), null)

  const replay = consoleRun({
    kind: 'replay',
    run: {
      scenario_id: 'SCEN0000', card_id: 'CA0001', delivered: 1, total: 1, running: false, next_at: null,
      ledger_run_id: 'replay-a', customer_id: 'CU0001', customer_name: 'Sofia Keller',
    },
  })!
  assert.equal(lastRunLine(replay), 'Last run: Replay · SCEN0000 · Sofia Keller (CU0001, card CA0001) · done')
  assert.equal(lastRunLine({ ...replay, customerId: null, customerName: null }), 'Last run: Replay · SCEN0000 · card CA0001 · done')
})

test('the run panel shows a run in progress; a finished one only for its selected scenario', () => {
  const live = consoleRun({
    kind: 'live',
    run: {
      run_id: 'r1', scenario_id: 'SCEN0136', card_id: 'CA1', state: 'running', delivered: 2, total: 10,
      decided: 2, pending_human: 0, last_error: null,
    },
  } as never)!
  // Running: shown whatever is selected.
  assert.equal(runInProgress(live, 0), true)
  assert.deepEqual(runPanel(live, 0, 'SCEN0001'), { run: live, finished: false })

  // Finished with step-ups waiting (counted from the stream, or D7's pending_human): still in progress.
  const done = { ...live, state: 'done' as const }
  assert.deepEqual(runPanel(done, 2, 'SCEN0001'), { run: done, finished: false })
  assert.deepEqual(runPanel({ ...done, pendingHuman: 1 }, 0, null), { run: { ...done, pendingHuman: 1 }, finished: false })

  // Finished, another scenario selected (or none): "No run in progress".
  assert.equal(runInProgress(done, 0), false)
  assert.equal(runPanel(done, 0, 'SCEN0001'), null)
  assert.equal(runPanel(done, 0, null), null)
  assert.equal(runPanel({ ...done, state: 'error' }, 0, 'SCEN0001'), null)

  // Finished, its own scenario selected: shown, labelled finished.
  assert.deepEqual(runPanel(done, 0, 'SCEN0136'), { run: done, finished: true })

  // No run at all.
  assert.equal(runPanel(null, 0, 'SCEN0136'), null)
})

test('a replay from record is marked as one, and names the live run it replays', () => {
  const fromRecord = consoleRun({
    kind: 'replay',
    run: {
      scenario_id: 'SCEN0101', card_id: 'CA1331', delivered: 1, total: 3, running: true, next_at: null,
      ledger_run_id: 'replay-b', customer_id: 'CU1217', customer_name: 'Omar Chen',
      source: 'record', record_run_id: 'run_42', record_started_at: '2026-09-25T10:00:00Z',
    },
  })!
  assert.deepEqual(fromRecord.fromRecord, { runId: 'run_42', startedAt: '2026-09-25T10:00:00Z' })
  assert.equal(fromRecord.policy, null) // an older backend: no policy source
  const withPolicy = consoleRun({
    kind: 'replay',
    run: {
      scenario_id: 'SCEN0101', card_id: 'CA1331', delivered: 0, total: 3, running: true, next_at: null,
      source: 'record', record_run_id: 'run_42', policy_source: 'card', mandate_id: 'mnd_1',
    },
  })!
  assert.deepEqual([runTitle(withPolicy), withPolicy.policy], ['Replay from record', "the card's active policy"])
  assert.equal(runTitle(fromRecord), 'Replay from record')
  assert.equal(lastRunLine(fromRecord), 'Last run: Replay from record · SCEN0101 · Omar Chen (CU1217, card CA1331) · running')

  const pack = consoleRun({
    kind: 'replay',
    run: { scenario_id: 'SCEN0000', card_id: 'CA0001', delivered: 0, total: 1, running: true, next_at: null, source: 'pack' },
  })!
  assert.equal(pack.fromRecord, null)
  assert.equal(runTitle(pack), 'Replay')
  assert.equal(runTitle({ kind: 'live', fromRecord: null }), 'Judging run')
})

test('D2 replays the pack, a served scenario from record, or says it has not run yet', () => {
  assert.deepEqual(replayGuard(summary({ replay_source: 'pack' })), { blocked: null, note: null })
  assert.deepEqual(replayGuard(summary({})), { blocked: null, note: null }) // an older backend: D2 says
  assert.deepEqual(replayGuard(null), { blocked: null, note: null })
  assert.equal(replayGuard(summary({ replay_source: null })).blocked, 'Not run yet: no stored events to replay.')
  const record = replayGuard(summary({ replay_source: 'record' }))
  assert.equal(record.blocked, null)
  assert.match(record.note ?? '', /^Replay from record: .*Nothing is sent to the platform\.$/)
})

test('the embedded phone shows the run panel’s customer, else the selected scenario’s, never a finished run’s under another', () => {
  const oliverRun = (running: boolean) =>
    consoleRun({
      kind: 'replay',
      run: {
        scenario_id: 'SCEN0004', card_id: 'CA0039', delivered: 11, total: 11, running, next_at: null,
        ledger_run_id: 'replay-o', customer_id: 'CU0019', customer_name: 'Oliver Graf',
      },
    })!
  const oliver = summary({ scenario_id: 'SCEN0004', customer_id: 'CU0019', customer_name: 'Oliver Graf', card_id: 'CA0039' })
  const livia = summary({ scenario_id: 'SCEN0136', customer_id: 'CU1475', customer_name: 'Livia Bachmann', card_id: 'CA1738' })
  const phone = (run: ReturnType<typeof oliverRun> | null, waiting: number, selected: ScenarioSummary | null) =>
    phoneCustomer(runPanel(run, waiting, selected?.scenario_id ?? null)?.run, selected).customerId

  // Oliver's run finished, Livia's scenario selected: Livia, not Oliver's passport under her name.
  assert.equal(phone(oliverRun(false), 0, livia), 'CU1475')
  // The finished run's own scenario selected: Oliver.
  assert.equal(phone(oliverRun(false), 0, oliver), 'CU0019')
  // A run in progress, or with step-ups waiting, keeps its customer on the phone to answer them.
  assert.equal(phone(oliverRun(true), 0, livia), 'CU0019')
  assert.equal(phone(oliverRun(false), 2, livia), 'CU0019')
  // No run; a scenario with no customer shows the picker.
  assert.equal(phone(null, 0, livia), 'CU1475')
  assert.equal(phone(null, 0, summary({ customer_id: null, customer_name: null })), null)
  assert.deepEqual(phoneCustomer(null, livia), { customerId: 'CU1475', customerName: 'Livia Bachmann' })
})

test('the embedded phone is keyed on its URL: another customer is a new iframe', () => {
  assert.equal(phoneEmbedSrc('CU0019'), '/?customer=CU0019&embed=1')
  assert.equal(phoneEmbedSrc(null), '/?embed=1')
  assert.notEqual(phoneEmbedSrc('CU0019'), phoneEmbedSrc('CU1475'))
  const phone = readFileSync(new URL('../src/ops/CustomerPhone.tsx', import.meta.url), 'utf8')
  assert.match(phone, /const embedSrc = phoneEmbedSrc\(customerId\)/)
  assert.match(phone, /key=\{embedSrc\}\s+title="Customer phone"\s+src=\{embedSrc\}/)
})
