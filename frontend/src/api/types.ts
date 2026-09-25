export interface Customer {
  customer_id: string
  name: string
  home_region: string
  // null for a customer with no scenario/mandate behind it (live: false) —
  // there's nothing to drive yet, so there's no card or scenario to name.
  card_id: string | null
  // One customer can back several scenarios (CU0001: SCEN0000 and SCEN0001);
  // empty for a customer with nothing behind it.
  scenario_ids: string[]
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
  // 'info' is context, never a pass or a fail; rendered neutrally, as is
  // any value this client doesn't know yet.
  outcome: 'pass' | 'fail' | 'uncertain' | 'info'
  detail: string
  source?: 'policy' | 'ledger' | 'history' | 'merchant_text' | 'model'
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
  // For icons only.
  kind?: 'amount' | 'period' | 'merchant' | 'item' | 'terms' | 'session' | 'other'
}

export interface DryRunResult {
  sample_size: number
  would_violate: number
  would_fit: number
  would_ask: number
  insight: string
  examples?: {
    occurred_at: string
    merchant_name: string
    billing_amount_chf: number
    outcome: 'fit' | 'violate' | 'ask'
    reason: string
  }[]
  // History rows with initiator_type 'agent', across the customer's cards.
  agent_history?: { attempts: number; approved: number }
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
  // 'fallback' = the LLM was unavailable and the rule-based parse was used.
  compiler?: 'llm' | 'form' | 'fallback'
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
  usage?: MandateUsage
}

// The engine ledger's own view of the mandate, authoritative when present
// (docs/api-contract.md §2).
export interface MandateUsage {
  per_order_limit_chf: number | null
  period_limit_chf: number | null
  period_days: number | null
  // Final approvals only, including human-approved step-ups.
  period_spent_chf: number
  // Simulated time, ISO 8601.
  period_window_start: string
  // Stepped-up, awaiting the customer; not spent.
  pending_chf: number
  fulfilment?: { bought: number; requested: number } | null
  // Simulated time of the last decision.
  as_of: string
  /**
   * Restrictions the customer has already answered for a shop and item, which
   * the engine remembers so it stops asking (`LedgerView.confirmed_keys`;
   * engine/policy.py `is_unverifiable` — only a restriction no data can check
   * can be passed this way). `../docs/api-contract.md` §2, rendered per §6
   * item 11. Field-for-field with the backend's `Confirmation`. Names are
   * untrusted text and render as plain text nodes.
   */
  confirmations?: { rule_text: string; merchant_name: string; item_name: string }[]
}

export type DecisionRelation = 'requote_of' | 'duplicate_of' | 'retry_of' | 'split_of'

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

  // Optional additions (docs/api-contract.md §2, §6), absent from older
  // payloads, so every reader must cope with them missing.
  counterfactual?: string | null
  related?: { authorization_id: string; relation: DecisionRelation } | null
  session?: { trust: 'normal' | 'elevated' | 'frozen'; note: string } | null
  // Trusted catalogue fields, not merchant text.
  merchant_meta?: {
    category: string
    country: string
    familiar: boolean
    prior_approvals_on_card: number
    prior_approvals_other_cards: number
  }
  engine_version?: string
  latency_ms?: number
  // 'model' once a tier-3 rewrite of `message` has landed.
  explanation_source?: 'template' | 'model'
  // Resolved step-ups only: a customer's answer or the window timing out.
  resolved_by?: 'customer' | 'timeout'
  /**
   * Present on a step-up whose deciding rule is one no data can check (a
   * restriction like "an official ticket seller" — engine/policy.py
   * `is_unverifiable`). Approving it can also be remembered for this shop and
   * item, which is what the confirm button then offers. `phrase` is the
   * restriction in the customer's own words, for that sentence.
   * PENDING: not yet in `../docs/api-contract.md` §2 or `api/models.py`;
   * requested from P1. Absent means the ordinary approve button.
   */
  confirmable?: { rule_id: string; phrase: string } | null
  // The run this decision belongs to, and that run's start on the real clock
  // (`../docs/api-contract.md` §2, §6 item 14). Activity and Home list a card's
  // newest run and fold older ones away (`lib/runs.ts`).
  run_id?: string
  run_started_at?: string
  // The rules this decision was checked against (`../docs/api-contract.md` §2,
  // §6 item 17). `platform` = the Viseca mandate's own rules, used when no
  // confirmed policy was bound to it. Absent: show the card's current policy.
  policy_applied?: {
    mandate_id: string
    source: 'confirmed' | 'platform'
    checks: RuleCheck[]
  } | null
}

// Operator-only shapes (`../docs/api-contract.md` §1.2 D1–D9), for the `?demo=1`
// strip and the `/ops` console. Field-for-field with the backend's `api/models.py`.
export interface ReplayStatus {
  scenario_id: string
  card_id: string
  delivered: number
  total: number
  running: boolean
  next_at: string | null
  // The run_id this replay's C6 decisions carry, its start (real clock), how
  // many purchases it has decided and who holds the card. Absent from older
  // backends.
  ledger_run_id?: string
  started_at?: string
  decided?: number
  customer_id?: string
  customer_name?: string
}

// D5 read: whether the models run now, for live runs and for the offline replay.
// The two differ until an operator sets D5 (§3.7).
export interface SoftSignalsState {
  live: boolean
  replay: boolean
}

export interface LiveRun {
  run_id: string
  scenario_id: string
  card_id: string
  mandate_id: string
  state: 'starting' | 'running' | 'done' | 'error'
  delivered: number
  decided: number
  pending_human: number
  total: number
  worker_ok: boolean
  last_error: string | null
  // Who holds card_id; the run_id its C6 decisions carry (run_id is the
  // platform's); its start, real clock. Absent from older backends.
  customer_id?: string
  customer_name?: string
  ledger_run_id?: string
  started_at?: string
}

// D9: one scenario of the store's catalogue, for the console's picker. The
// customer and card are null until something names the scenario's card.
export interface ScenarioSummary {
  scenario_id: string
  name: string
  event_count: number
  // The cardholder instruction, verbatim.
  instruction: string
  customer_id: string | null
  customer_name: string | null
  card_id: string | null
}

export interface LedgerSnapshotEntry {
  authorization_id: string
  occurred_at: string
  decision: DecisionOutcome
  counted_chf: number
  note: string
}

export interface LedgerSnapshot {
  card_id: string
  mandate_id: string
  entries: LedgerSnapshotEntry[]
  period_spent_chf: number
  frozen: boolean
}
