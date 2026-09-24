import type { DryRunResult, FormInput, Mandate, PolicyDraft, RuleCheck } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function normalize(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ')
}

// Typing this phrase into the instruction textarea is the demo's way to
// reach NewPolicyTimeout without a real model call to time out (mock mode
// has no model). Matched on the customer's own instruction text, never on
// a scenario id or name.
const TIMEOUT_TRIGGER = 'force timeout'

const EMPTY_DRY_RUN: DryRunResult = {
  sample_size: 0,
  would_violate: 0,
  would_fit: 0,
  would_ask: 0,
  insight: "We don't have enough matching purchase history to run a dry check yet.",
}

function amountLimitFromText(instruction: string): number | null {
  const match = instruction.match(/CHF\s*([\d,]+(?:\.\d+)?)/i)
  if (!match) return null
  return Number(match[1].replace(/,/g, ''))
}

function fallbackDraft(cardId: string, instruction: string): PolicyDraft {
  const limit = amountLimitFromText(instruction)
  const checks: RuleCheck[] = limit
    ? [{ id: 'amount', text: `Total at or below CHF ${limit} per order`, source: 'exact', uncertainty: null }]
    : []

  return {
    draft_id: `draft-fallback-${Date.now()}`,
    card_id: cardId,
    instruction,
    checks,
    uncertainty_policy: 'ask',
    open_questions: [
      "This instruction doesn't match anything we've tested yet — read the checks over carefully before confirming.",
    ],
    dry_run: EMPTY_DRY_RUN,
  }
}

function formToDraft(cardId: string, form: FormInput): PolicyDraft {
  const checks: RuleCheck[] = []
  if (form.per_order_limit_chf != null) {
    checks.push({
      id: 'per_order',
      text: `Total at or below CHF ${form.per_order_limit_chf} per order`,
      source: 'exact',
      uncertainty: null,
    })
  }
  if (form.period_limit_chf != null && form.period_days != null) {
    checks.push({
      id: 'period',
      text: `Total at or below CHF ${form.period_limit_chf} across any ${form.period_days} days`,
      source: 'exact',
      uncertainty: null,
    })
  }
  if (form.categories.length > 0) {
    checks.push({
      id: 'category',
      text: `Only these purchase types: ${form.categories.join(', ')}`,
      source: 'exact',
      uncertainty: null,
    })
  }
  checks.push({
    id: 'sellers',
    text: form.sellers_used_before_only ? 'From sellers you have used before' : 'Any seller',
    source: 'exact',
    uncertainty: null,
  })

  return {
    draft_id: `draft-form-${Date.now()}`,
    card_id: cardId,
    instruction: '', // form path has no free text — the checks above are the whole policy
    checks,
    uncertainty_policy: form.uncertainty_policy,
    open_questions: [],
    dry_run: EMPTY_DRY_RUN,
  }
}

/**
 * Turns an instruction (AI-read path) or a form (AI-off path) into a
 * proposed policy (contract capability C1, endpoint proposed — not yet
 * backend-ratified). Both paths hit the backend once a real one exists —
 * only the AI-read path costs a compile delay in mock mode; DESIGN.md's
 * `PolicyInput` spec says the form path is instant, not that it skips the
 * network entirely (the real backend still needs to register the draft
 * under a real `draft_id` before it can be confirmed).
 */
export async function compilePolicy(
  cardId: string,
  input: { instruction: string } | { form: FormInput },
): Promise<PolicyDraft> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    if ('form' in input) {
      return formToDraft(cardId, input.form)
    }
    const normalized = normalize(input.instruction)
    await delay(900)
    if (normalized.includes(TIMEOUT_TRIGGER)) {
      throw new Error('Reading the instruction took too long')
    }
    const { drafts } = await import('../mocks/fixtures/policy-drafts.json')
    const match = (drafts as PolicyDraft[]).find((d) => normalize(d.instruction) === normalized)
    if (match) {
      return { ...match, draft_id: `${match.draft_id}-${cardId}`, card_id: cardId }
    }
    return fallbackDraft(cardId, input.instruction)
  }

  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/policy-drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!response.ok) {
    throw new Error(`Failed to compile policy (${response.status})`)
  }
  return (await response.json()) as PolicyDraft
}

/**
 * Confirms a proposed policy after the customer agrees (C2). Sends the
 * (possibly customer-edited, e.g. the uncertainty_policy toggle on the
 * review screen) draft content as the request body — real Viseca mandate
 * confirms take no body (`{"confirmed":true}` only, technical_details.md
 * §5), so a real backend behind this endpoint is expected to re-register
 * the edited content as a fresh Viseca mandate draft and confirm that,
 * hiding the two-step dance from the frontend. Our own contract is a
 * wrapper, not a 1:1 proxy of Viseca's raw endpoints (see C12).
 */
export async function confirmPolicy(draft: PolicyDraft): Promise<Mandate> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return {
      mandate_id: `TM-mock-${draft.draft_id}`,
      card_id: draft.card_id,
      instruction: draft.instruction,
      checks: draft.checks,
      uncertainty_policy: draft.uncertainty_policy,
      open_questions: draft.open_questions,
      status: 'active',
      confirmed_at: new Date().toISOString(),
    }
  }

  const response = await fetch(`${API_BASE_URL}/policy-drafts/${draft.draft_id}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      checks: draft.checks,
      uncertainty_policy: draft.uncertainty_policy,
      open_questions: draft.open_questions,
    }),
  })
  if (!response.ok) {
    throw new Error(`Failed to confirm policy (${response.status})`)
  }
  return (await response.json()) as Mandate
}

/**
 * Tightens an active policy (C4): add checks, optionally move
 * `uncertainty_policy` to `decline`. Never removes or edits an existing
 * check — the backend is expected to reject anything else, same as the
 * real Viseca `PATCH /v1/mandates/{id}` (technical_details.md §8: "keep
 * every existing rule unchanged... may add"). The UI itself also never
 * offers a control that could send a loosening request in the first place
 * (frontend/.claude/CLAUDE.md hard rule 3).
 */
export async function tightenPolicy(
  mandate: Mandate,
  additions: { addChecks: RuleCheck[]; uncertaintyPolicy?: 'decline' },
): Promise<Mandate> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return {
      ...mandate,
      checks: [...mandate.checks, ...additions.addChecks],
      uncertainty_policy: additions.uncertaintyPolicy ?? mandate.uncertainty_policy,
    }
  }

  const response = await fetch(`${API_BASE_URL}/cards/${mandate.card_id}/policy/tighten`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      add_checks: additions.addChecks,
      uncertainty_policy: additions.uncertaintyPolicy,
    }),
  })
  if (!response.ok) {
    throw new Error(`Failed to tighten policy (${response.status})`)
  }
  return (await response.json()) as Mandate
}

/**
 * Revokes a card's policy (C5). Only ever the policy itself — this never
 * touches any pending decision on that card; the effect of a revoke on
 * queued or pending purchases is unspecified by the platform
 * (docs/api-contract.md §3.6), so nothing here invents one.
 */
export async function revokePolicy(cardId: string): Promise<void> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return
  }

  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/policy/revoke`, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(`Failed to revoke policy (${response.status})`)
  }
}

/**
 * Reads the current policy for a card (C3). In mock mode there is no
 * persisted backend state, so this always resolves `null` — confirming a
 * policy updates the session's own `PolicyContext` directly from
 * `confirmPolicy`'s result instead of refetching.
 */
export async function getPolicy(cardId: string): Promise<Mandate | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return null
  }

  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/policy`)
  if (!response.ok) {
    throw new Error(`Failed to load policy (${response.status})`)
  }
  const data = (await response.json()) as { mandate: Mandate | null }
  return data.mandate
}
