import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isKnownReasonCode, reasonLabel } from '../src/lib/reasonCodes.ts'

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
