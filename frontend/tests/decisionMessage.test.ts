import assert from 'node:assert/strict'
import { test } from 'node:test'
import { messageWithoutCounterfactual, noteAddsToMessage } from '../src/lib/decisionMessage.ts'

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

test('a pending step-up message ending with its counterfactual is said once on Approvals', () => {
  const counterfactual = 'Would approve if it is not a repeat of the earlier order.'
  assert.equal(
    messageWithoutCounterfactual(
      `Asking about CHF 64.00: this looks like a repeat of an order you already placed. ${counterfactual}`,
      counterfactual,
    ),
    'Asking about CHF 64.00: this looks like a repeat of an order you already placed.',
  )
})

test('a note that restates the message clause adds nothing (AU0036)', () => {
  assert.equal(
    noteAddsToMessage(
      'Waiting for you CHF 289.00: Same shop and items as the CHF 289.00 order 25 min earlier.',
      'Same shop and items as the CHF 289.00 order 25 min earlier (CHF 289.00 then, CHF 289.00 now).',
    ),
    false,
  )
})

test('a note that rewords the message clause adds nothing (AU0040)', () => {
  assert.equal(
    noteAddsToMessage(
      "Waiting for you CHF 299.00: The shop's text had instructions aimed at the agent; they were ignored, so you decide.",
      "The shop's text contains instructions aimed at the agent (line 1 details); they were ignored.",
    ),
    false,
  )
})

test('a note naming what the message does not is shown', () => {
  assert.equal(
    noteAddsToMessage(
      'Waiting for you CHF 80.00: your rules could not settle this purchase.',
      'The shop does not say which size this is; you asked for size 42.',
    ),
    true,
  )
})

test('an empty or identical note adds nothing', () => {
  const message = 'Waiting for you CHF 80.00: the size is not stated.'
  assert.equal(noteAddsToMessage(message, null), false)
  assert.equal(noteAddsToMessage(message, '  '), false)
  assert.equal(noteAddsToMessage(message, message), false)
})
