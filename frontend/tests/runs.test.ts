import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Decision } from '../src/api/types.ts'
import { formatRunStart, splitByRun } from '../src/lib/runs.ts'

function decision(id: string, card: string, run?: string, started?: string): Decision {
  return {
    authorization_id: id,
    card_id: card,
    run_id: run,
    run_started_at: started,
  } as Decision
}

const ids = (rows: Decision[]) => rows.map((d) => d.authorization_id)

test('only the newest run on a card is current; older runs fold away newest first', () => {
  const rows = [
    decision('a1', 'CA1', 'r1', '2026-09-24T10:00:00Z'),
    decision('c1', 'CA1', 'r3', '2026-09-24T12:00:00Z'),
    decision('b1', 'CA1', 'r2', '2026-09-24T11:00:00Z'),
    decision('c2', 'CA1', 'r3', '2026-09-24T12:00:00Z'),
    decision('a2', 'CA1', 'r1', '2026-09-24T10:00:00Z'),
  ]
  const { current, earlier } = splitByRun(rows)
  assert.deepEqual(ids(current), ['c1', 'c2'])
  assert.deepEqual(
    earlier.map((run) => [run.startedAt, ids(run.decisions)]),
    [
      ['2026-09-24T11:00:00Z', ['b1']],
      ['2026-09-24T10:00:00Z', ['a1', 'a2']],
    ],
  )
})

test('each card keeps its own newest run', () => {
  const rows = [
    decision('x1', 'CA1', 'r1', '2026-09-24T10:00:00Z'),
    decision('y1', 'CA2', 'r2', '2026-09-24T09:00:00Z'),
    decision('x2', 'CA1', 'r3', '2026-09-24T11:00:00Z'),
  ]
  const { current, earlier } = splitByRun(rows)
  assert.deepEqual(ids(current), ['y1', 'x2'])
  assert.deepEqual(
    earlier.map((run) => [run.cardId, ids(run.decisions)]),
    [['CA1', ['x1']]],
  )
})

test('a decision without a run id is never hidden', () => {
  const rows = [
    decision('old', 'CA1'),
    decision('r1', 'CA1', 'r1', '2026-09-24T10:00:00Z'),
    decision('r2', 'CA1', 'r2', '2026-09-24T11:00:00Z'),
  ]
  const { current, earlier } = splitByRun(rows)
  assert.deepEqual(ids(current), ['old', 'r2'])
  assert.deepEqual(earlier.map((run) => ids(run.decisions)), [['r1']])
})

test('no run ids at all: everything is current', () => {
  const rows = [decision('a', 'CA1'), decision('b', 'CA1')]
  assert.deepEqual(splitByRun(rows), { current: rows, earlier: [] })
})

test('a run without a start sorts oldest and keeps a null start', () => {
  const rows = [
    decision('n1', 'CA1', 'rn'),
    decision('s1', 'CA1', 'rs', '2026-09-24T10:00:00Z'),
    decision('t1', 'CA1', 'rt', '2026-09-24T09:00:00Z'),
  ]
  const { current, earlier } = splitByRun(rows)
  assert.deepEqual(ids(current), ['s1'])
  assert.deepEqual(
    earlier.map((run) => [run.startedAt, ids(run.decisions)]),
    [
      ['2026-09-24T09:00:00Z', ['t1']],
      [null, ['n1']],
    ],
  )
})

test('a run start reads as a short day and a 24h time in the given zone', () => {
  assert.equal(formatRunStart('2026-09-24T12:03:00Z', 'Europe/Zurich'), 'Thu 24 Sep · 14:03')
  assert.equal(formatRunStart('2026-09-24T23:30:00Z', 'Europe/Zurich'), 'Fri 25 Sep · 01:30')
  assert.equal(formatRunStart('2026-09-24T00:05:00Z', 'UTC'), 'Thu 24 Sep · 00:05')
})
