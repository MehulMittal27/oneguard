import type { DryRunResult, FormInput, Mandate, MandateUsage, PolicyDraft, RuleCheck } from './types'
import { signedFetch } from '../lib/deviceKey'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

// Mock mode's stand-in for the backend's mandate store: C2 writes it, C5 flips
// it, C3 reads it. The UI only ever learns a card's policy from C3, so mock mode
// needs something for C3 to read, or every refresh would erase the policy just
// confirmed. Lives as long as the page, like everything else in mock mode.
const mockMandates = new Map<string, Mandate>()

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
    // This function is the rule-based parse the contract's 'fallback' means, so
    // it says so. In mock mode any unrecognised instruction lands here, which is
    // what exercises the review screen's banner.
    compiler: 'fallback',
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
    // The customer typed the rules themselves; nothing read anything.
    compiler: 'form',
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
 * wrapper, not a 1:1 proxy of Viseca's raw endpoints (see C12). Signed by
 * this device, which must be enrolled on the draft's card (contract §3.10).
 */
export async function confirmPolicy(draft: PolicyDraft): Promise<Mandate> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    const mandate: Mandate = {
      mandate_id: `TM-mock-${draft.draft_id}`,
      card_id: draft.card_id,
      instruction: draft.instruction,
      checks: draft.checks,
      uncertainty_policy: draft.uncertainty_policy,
      open_questions: draft.open_questions,
      status: 'active',
      confirmed_at: new Date().toISOString(),
      // Fixture drafts carry the mandate's ledger view (build_policy_fixture.py);
      // form and fallback drafts don't, so those fall back to the check wording.
      usage: (draft as PolicyDraft & { usage?: MandateUsage }).usage,
    }
    // A confirmed draft replaces the card's policy (docs/api-contract.md §3.2).
    mockMandates.set(draft.card_id, mandate)
    return mandate
  }

  const response = await signedFetch(draft.card_id, 'POST', `/policy-drafts/${draft.draft_id}/confirm`, {
    checks: draft.checks,
    uncertainty_policy: draft.uncertainty_policy,
    open_questions: draft.open_questions,
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
    const tightened: Mandate = {
      ...mandate,
      checks: [...mandate.checks, ...additions.addChecks],
      uncertainty_policy: additions.uncertaintyPolicy ?? mandate.uncertainty_policy,
    }
    mockMandates.set(mandate.card_id, tightened)
    return tightened
  }

  const response = await signedFetch(mandate.card_id, 'POST', `/cards/${mandate.card_id}/policy/tighten`, {
    add_checks: additions.addChecks,
    uncertainty_policy: additions.uncertaintyPolicy,
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
    // Flipped, never deleted — C3 keeps returning a revoked policy (§3.6).
    const mandate = mockMandates.get(cardId)
    if (mandate) mockMandates.set(cardId, { ...mandate, status: 'revoked' })
    return
  }

  const response = await signedFetch(cardId, 'POST', `/cards/${cardId}/policy/revoke`)
  if (!response.ok) {
    throw new Error(`Failed to revoke policy (${response.status})`)
  }
}

/**
 * Reads a card's policy (C3): the active mandate, else its latest revoked one,
 * with `usage` from the engine ledger; `null` only when the card never had a
 * policy. This is where the UI learns every card's policy, on sign-in and on
 * every refresh (see `PolicyProvider`). In mock mode it reads the page-lifetime
 * store that the mock C2/C4/C5 write.
 */
export async function getPolicy(cardId: string): Promise<Mandate | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return mockMandates.get(cardId) ?? null
  }

  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/policy`)
  if (!response.ok) {
    throw new Error(`Failed to load policy (${response.status})`)
  }
  const data = (await response.json()) as { mandate: Mandate | null }
  return data.mandate
}
