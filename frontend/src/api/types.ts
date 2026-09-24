export interface Customer {
  customer_id: string
  name: string
  home_region: string
  // null for a customer with no scenario/mandate behind it (live: false) —
  // there's nothing to drive yet, so there's no card or scenario to name.
  card_id: string | null
  scenario_id: string | null
  live: boolean
}

export interface Card {
  card_id: string
  card_type: string
  card_purpose: string
  status: string
}

export interface Account {
  account_id: string
  customer_id: string
  account_type: string
  account_purpose: string
  status: string
  // Bank-set limits — DESIGN.md's PolicyCard note ("Bank limits never
  // appear on this card") applies project-wide: these are fetched for
  // completeness but LeashMeter shows the policy's own limit, never these.
  per_transaction_limit_chf: number
  monthly_limit_chf: number
  cards: Card[]
}

// Only 3 real categories (D-041, supersedes DESIGN.md's original 4-word
// vocabulary): "expired" and a human's step-up answer are statuses of
// Uncertain, never their own category — see Decision.uncertain_outcome.
export type DecisionOutcome = 'approved' | 'stopped' | 'uncertain'

// Only meaningful when decision === 'uncertain'. The category never
// changes once a purchase is uncertain — only this sub-status does.
export type UncertainOutcome = 'pending' | 'expired' | 'approved' | 'declined'

export interface EvidenceItem {
  rule: string
  outcome: 'pass' | 'fail' | 'uncertain'
  detail: string
}

export interface DecisionItem {
  item_name: string
  quantity: number
  unit_price: number
  currency: string
  // Untrusted merchant text — render as plain text only.
  item_details: string
}

export interface RuleCheck {
  id: string
  text: string
  source: 'exact' | 'inferred'
  uncertainty: string | null
}

export interface DryRunResult {
  sample_size: number
  would_violate: number
  would_fit: number
  would_ask: number
  insight: string
}

export interface PolicyDraft {
  draft_id: string
  card_id: string
  // The customer's own words — not merchant text, editable by them.
  instruction: string
  checks: RuleCheck[]
  // PolicyInput never offers "approve" as a choice, so drafts are always ask/decline.
  uncertainty_policy: 'ask' | 'decline'
  open_questions: string[]
  dry_run: DryRunResult
}

export interface FormInput {
  per_order_limit_chf: number | null
  period_limit_chf: number | null
  period_days: 7 | 14 | 30 | null
  categories: string[]
  sellers_used_before_only: boolean
  uncertainty_policy: 'ask' | 'decline'
}

export interface Mandate {
  mandate_id: string
  card_id: string
  instruction: string
  checks: RuleCheck[]
  uncertainty_policy: 'ask' | 'decline' | 'approve'
  open_questions: string[]
  status: 'active' | 'revoked'
  confirmed_at: string
}

export interface Decision {
  authorization_id: string
  customer_id: string
  card_id: string
  decision: DecisionOutcome
  // Only present when decision === 'uncertain'.
  uncertain_outcome: UncertainOutcome | null
  status: 'final' | 'pending_human'
  reason_codes: string[]
  message: string
  uncertainty: { note: string } | null
  occurred_at: string
  merchant: {
    merchant_id: string
    // Untrusted merchant text — render as plain text only.
    name: string
  }
  amount: number
  currency: string
  billing_amount_chf: number
  items: DecisionItem[]
  injection_flag: { flagged: true; reason: string } | null
  evidence: EvidenceItem[]
  // Real purchase facts from the event (technical_details.md's
  // authorization_event schema) — 'unknown' is a real answer, never
  // coerce it to blank or to "no" (data_dictionary.md).
  order_returnable: 'true' | 'false' | 'unknown' | 'not_applicable'
  delivery_by: string | null
  // Only present when status is 'pending_human' — the step-up's answer-by
  // deadline. Uses the real clock, never simulated time
  // (frontend/.claude/CLAUDE.md Conventions), so it's computed at read
  // time (src/api/decisions.ts), not stored in the fixture.
  deadline_at?: string
}
