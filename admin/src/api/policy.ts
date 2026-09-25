import { api } from './client'
import type { Mandate, PolicyDraft } from './types'

/**
 * C1/C2, used only to provision a policy for a scenario that has none yet —
 * the same two calls `make demo-live` makes before D3 (`docs/api-contract.md`
 * §1.2 "make demo-live"). Never used to edit an existing policy: that stays
 * the customer's own action in `frontend/`, per root CLAUDE.md rule 7
 * ("tightening only" is enforced there, not reproduced here).
 */
export function draftFromInstruction(cardId: string, instruction: string): Promise<PolicyDraft> {
  return api.post<PolicyDraft>(`/cards/${encodeURIComponent(cardId)}/policy-drafts`, { instruction })
}

export function confirmDraft(draftId: string, draft: PolicyDraft): Promise<Mandate> {
  return api.post<Mandate>(`/policy-drafts/${encodeURIComponent(draftId)}/confirm`, {
    checks: draft.checks.map((c) => c.id),
    uncertainty_policy: draft.uncertainty_policy,
    open_questions: draft.open_questions,
  })
}
