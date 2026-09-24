import type { PolicyDraft } from '../api/types'

/**
 * What the review screen asks when no check at all was read ("buy something
 * nice"). The backend sends the same text first in `open_questions` (contract
 * §3.2); this copy is the fallback for a draft that carries no question.
 */
export const NO_CHECKS_QUESTION =
  "I couldn't read a spending limit or item type - try 'groceries, max CHF 120 per order'"

/** C2 refuses a draft with no checks, so the screen never offers to confirm one. */
export function canConfirmDraft(draft: Pick<PolicyDraft, 'checks'>): boolean {
  return draft.checks.length > 0
}

/**
 * The open questions to show under the checks: the backend's when it sent any,
 * otherwise, for a draft with no checks, the question that says what to write.
 */
export function reviewQuestions(draft: Pick<PolicyDraft, 'checks' | 'open_questions'>): string[] {
  if (draft.open_questions.length > 0) return draft.open_questions
  return canConfirmDraft(draft) ? [] : [NO_CHECKS_QUESTION]
}

/**
 * Contract §6 item 9's agent-history line. `agent_history` is counted across the
 * customer's cards (`HistoryIndex.agent_history(customer_id)`), while the dry
 * run above it is card-scoped, so the line names its own scope: in the pack the
 * two differ materially (CA0001: 14 on the card, 29 across the customer). Zero
 * attempts is worth saying: this would be the customer's first agent purchase.
 */
export function agentHistoryLine({ attempts, approved }: { attempts: number; approved: number }): string {
  if (attempts === 0) return 'No agent has tried to buy on any of your cards before.'
  const times = attempts === 1 ? 'once' : `${attempts} times`
  return `Across your cards, an agent has tried to buy ${times} before: ${approved} approved.`
}
