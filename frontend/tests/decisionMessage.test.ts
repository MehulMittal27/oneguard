import assert from 'node:assert/strict'
import { test } from 'node:test'
import { messageWithoutCounterfactual } from '../src/lib/decisionMessage.ts'

const suggestion = 'Would approve at CHF 20.00 or less.'

test('a message ending with the counterfactual drops that trailing suggestion', () => {
  assert.equal(
    messageWithoutCounterfactual(
      `Declined CHF 38.90: over your CHF 20.00 per-order limit. ${suggestion}`,
      suggestion,
    ),
    'Declined CHF 38.90: over your CHF 20.00 per-order limit.',
  )
})

test('a message without the suggestion is shown unchanged', () => {
  const refined = 'This was more than you allowed for one order.'
  assert.equal(messageWithoutCounterfactual(refined, suggestion), refined)
})

test('no counterfactual leaves the message unchanged', () => {
  const approved = 'Approved CHF 12.00: within your limits.'
  assert.equal(messageWithoutCounterfactual(approved, null), approved)
  assert.equal(messageWithoutCounterfactual(approved, undefined), approved)
  assert.equal(messageWithoutCounterfactual(approved, '  '), approved)
})

test('a message that is only the suggestion is never emptied', () => {
  assert.equal(messageWithoutCounterfactual(suggestion, suggestion), suggestion)
})

test('the suggestion is only cut at a word boundary', () => {
  const message = 'Declined: xWould approve at CHF 20.00 or less.'
  assert.equal(messageWithoutCounterfactual(message, suggestion), message)
})
