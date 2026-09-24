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
