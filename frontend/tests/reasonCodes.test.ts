import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { isKnownReasonCode, knownReasonCodes, reasonLabel } from '../src/lib/reasonCodes.ts'

test('no_purchase_history has its own customer label', () => {
  assert.equal(reasonLabel('no_purchase_history'), 'You have no purchase history yet')
})

test('period_count_exceeded has its own customer label', () => {
  assert.ok(isKnownReasonCode('period_count_exceeded'))
  assert.equal(reasonLabel('period_count_exceeded'), 'More orders than you allowed for this period')
})

test('an unknown code falls back to the neutral line', () => {
  assert.equal(reasonLabel('not_a_code'), 'Another check your rules ran')
})

// The §4 vocabulary, parsed the way backend/tests/test_protections_explain.py
// parses it: the "Existing:" and "Added:" lists plus the development-only code.
function contractReasonCodes(): Set<string> {
  const contract = readFileSync(new URL('../../docs/api-contract.md', import.meta.url), 'utf-8')
  const section = contract.split('## 4. Reason codes')[1].split('\n## ')[0]
  const lists = section.split('\n\n').filter((p) => p.startsWith('Existing:') || p.startsWith('Added:'))
  const codes = [...lists.join('\n').matchAll(/`([a-z_]+)`/g)].map((m) => m[1])
  const dev = [...section.matchAll(/Development only: `([a-z_]+)`/g)].map((m) => m[1])
  return new Set([...codes, ...dev])
}

test('every label is a §4 code and every §4 code has a label', () => {
  const contract = contractReasonCodes()
  assert.ok(contract.size > 20, 'parsed the §4 list')
  assert.deepEqual(new Set(knownReasonCodes()), contract)
})

test('every reason code in the mock decisions is a labelled §4 code', () => {
  const fixture = JSON.parse(
    readFileSync(new URL('../src/mocks/fixtures/decisions.json', import.meta.url), 'utf-8'),
  )
  const unknown = JSON.stringify(fixture)
    .match(/"reason_codes":\[[^\]]*\]/g)!
    .flatMap((list) => JSON.parse(list.slice('"reason_codes":'.length)) as string[])
    .filter((code) => !isKnownReasonCode(code))
  assert.deepEqual(unknown, [])
})
