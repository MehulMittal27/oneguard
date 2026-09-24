import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { RuleCheck } from '../src/api/types.ts'
import { agentHistoryLine, canConfirmDraft, NO_CHECKS_QUESTION, reviewQuestions } from '../src/lib/policyReview.ts'

const check: RuleCheck = { id: 'C1', text: 'Total ≤ CHF 120', source: 'exact', uncertainty: null }

test('a draft with no checks cannot be confirmed', () => {
  assert.equal(canConfirmDraft({ checks: [] }), false)
  assert.equal(canConfirmDraft({ checks: [check] }), true)
})

test("a draft with no checks shows the backend's open questions when it sent any", () => {
  const fromBackend = [NO_CHECKS_QUESTION, 'What should the agent buy?']
  assert.deepEqual(reviewQuestions({ checks: [], open_questions: fromBackend }), fromBackend)
})

test('a draft with no checks and no questions falls back to the same question', () => {
  assert.deepEqual(reviewQuestions({ checks: [], open_questions: [] }), [NO_CHECKS_QUESTION])
  assert.equal(
    NO_CHECKS_QUESTION,
    "I couldn't read a spending limit or item type - try 'groceries, max CHF 120 per order'",
  )
})

test('a draft with checks shows only what the backend asked', () => {
  assert.deepEqual(reviewQuestions({ checks: [check], open_questions: [] }), [])
  assert.deepEqual(reviewQuestions({ checks: [check], open_questions: ['q'] }), ['q'])
})

test('the agent-history line names its customer-wide scope', () => {
  assert.equal(
    agentHistoryLine({ attempts: 29, approved: 21 }),
    'Across your cards, an agent has tried to buy 29 times before: 21 approved.',
  )
  assert.equal(agentHistoryLine({ attempts: 1, approved: 0 }), 'Across your cards, an agent has tried to buy once before: 0 approved.')
  assert.equal(agentHistoryLine({ attempts: 0, approved: 0 }), 'No agent has tried to buy on any of your cards before.')
})
