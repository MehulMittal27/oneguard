import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Decision, Mandate, MandateUsage } from '../src/api/types.ts'
import { countDecisions, splitByRun } from '../src/lib/runs.ts'
import { policyUse } from '../src/lib/spend.ts'

// A SCEN0117-shaped run: 13 simulated days on one card, the approvals early,
// the declines late, after an earlier run of the same purchases.
function decision(
  id: string,
  occurredAt: string,
  outcome: Decision['decision'],
  amount: number,
  extra: Partial<Decision> = {},
): Decision {
  return {
    authorization_id: id,
    card_id: 'CA1559',
    decision: outcome,
    uncertain_outcome: null,
    status: 'final',
    occurred_at: occurredAt,
    billing_amount_chf: amount,
    run_id: 'run-new',
    run_started_at: '2026-09-25T01:02:32Z',
    ...extra,
  } as Decision
}

const run: Decision[] = [
  decision('a', '2026-08-27T09:15:00Z', 'uncertain', 60.61, { uncertain_outcome: 'approved' }),
  decision('b', '2026-08-28T10:50:00Z', 'stopped', 47.95),
  decision('c', '2026-08-29T06:45:00Z', 'approved', 62.3),
  decision('d', '2026-08-30T13:35:00Z', 'approved', 71.9),
  decision('e', '2026-08-31T07:40:00Z', 'approved', 22.84),
  decision('f', '2026-09-04T18:25:00Z', 'uncertain', 24, { uncertain_outcome: 'declined' }),
  decision('g', '2026-09-08T11:25:00Z', 'stopped', 102),
]
const earlier = run.map((d) => ({
  ...d,
  authorization_id: `old-${d.authorization_id}`,
  run_id: 'run-old',
  run_started_at: '2026-09-24T10:00:00Z',
}))

// What Home's OverviewHero and Activity's chips each compute from the one feed.
const homeCounts = (feed: Decision[]) => countDecisions(splitByRun(feed).current)
const activityCounts = (feed: Decision[]) => countDecisions(splitByRun(feed).current)

test("Home's counts equal Activity's chips, however many simulated days the run spans", () => {
  const feed = [...earlier, ...run]
  assert.deepEqual(homeCounts(feed), activityCounts(feed))
  assert.deepEqual(homeCounts(feed), { all: 7, approved: 3, stopped: 2, uncertain: 2 })
})

test('each new decision in the feed moves the counts on the next derivation', () => {
  const partial = [...earlier, ...run.slice(0, 4)]
  assert.equal(homeCounts(partial).approved, 2)
  assert.equal(homeCounts([...partial, run[4]]).approved, 3)
  assert.deepEqual(homeCounts([...partial, run[4]]), activityCounts([...partial, run[4]]))
})

const PER_ORDER_ONLY = [
  { id: 'C1', text: 'Total at or below CHF 100 per order', source: 'exact', uncertainty: null },
] as Mandate['checks']

function mandate(usage?: Partial<MandateUsage>): Mandate {
  return {
    mandate_id: 'md_1',
    card_id: 'CA1559',
    instruction: 'Groceries, max CHF 100 per order.',
    checks: PER_ORDER_ONLY,
    uncertainty_policy: 'ask',
    open_questions: [],
    status: 'active',
    confirmed_at: '2026-09-25T01:02:31Z',
    usage: usage && {
      per_order_limit_chf: 100,
      period_limit_chf: null,
      period_days: null,
      period_spent_chf: 0,
      period_window_start: '2026-08-27T09:15:00Z',
      pending_chf: 0,
      as_of: '2026-09-08T11:25:00Z',
      ...usage,
    },
  }
}

test('a policy with no period limit shows the run: approvals counted, spend from the ledger', () => {
  const current = splitByRun([...earlier, ...run]).current
  const use = policyUse(mandate({ period_spent_chf: 217.65 }), current, 'CA1559')
  assert.deepEqual(use, { approved: 4, spentChf: 217.65, pendingChf: 0, period: null })
})

test('without usage (mock mode) the spend is summed from the newest run only', () => {
  const current = splitByRun([...earlier, ...run]).current
  const use = policyUse(mandate(), current, 'CA1559')
  assert.equal(use.approved, 4)
  assert.equal(Math.round(use.spentChf * 100) / 100, 217.65)
  assert.equal(use.period, null)
})

test('a period policy reads its limit and window from usage', () => {
  const use = policyUse(
    mandate({ period_limit_chf: 300, period_days: 7, period_spent_chf: 94.74, pending_chf: 24 }),
    run,
    'CA1559',
  )
  assert.deepEqual(use.period, { limitChf: 300, days: 7 })
  assert.equal(use.spentChf, 94.74)
  assert.equal(use.pendingChf, 24)
})

test("a period policy counts only the purchases in the ledger's window, like its spend", () => {
  const use = policyUse(
    mandate({
      period_limit_chf: 300,
      period_days: 7,
      period_spent_chf: 94.74,
      period_window_start: '2026-08-30T00:00:00Z',
      as_of: '2026-09-08T11:25:00Z',
    }),
    run,
    'CA1559',
  )
  assert.equal(use.approved, 2)
})

test('without usage a period policy windows the count the way computePeriodSpend windows spend', () => {
  const periodChecks = [
    ...PER_ORDER_ONLY,
    { id: 'C2', text: 'Total at or below CHF 300 across any 2 days', source: 'exact', uncertainty: null },
  ] as Mandate['checks']
  const use = policyUse({ ...mandate(), checks: periodChecks }, run, 'CA1559')
  assert.deepEqual(use.period, { limitChf: 300, days: 2 })
  assert.equal(use.approved, 2)
  assert.equal(Math.round(use.spentChf * 100) / 100, 94.74)
})
