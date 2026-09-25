/**
 * Types mirror `docs/api-contract.md` §2 field-for-field (source of truth,
 * alongside `backend/oneguard/api/models.py`). Copied rather than imported
 * from `frontend/src/api/types.ts` — this is a separate deployable, per
 * `styles/tokens.css`'s note. Trim to what the console reads; add fields as
 * screens need them rather than mirroring the whole contract speculatively.
 */

export interface Customer {
  customer_id: string
  name: string
  home_region: string
  card_id: string | null
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
  per_transaction_limit_chf: number
  monthly_limit_chf: number
  cards: Card[]
}

export interface RuleCheck {
  id: string
  text: string
  source: 'exact' | 'inferred'
  uncertainty: string | null
  kind?: 'amount' | 'period' | 'merchant' | 'item' | 'terms' | 'session' | 'other'
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
  instruction: string
  checks: RuleCheck[]
  uncertainty_policy: 'ask' | 'decline'
  open_questions: string[]
  dry_run: DryRunResult
  compiler?: 'llm' | 'form' | 'fallback'
}

export interface MandateUsage {
  per_order_limit_chf: number | null
  period_limit_chf: number | null
  period_days: number | null
  period_spent_chf: number
  period_window_start: string
  pending_chf: number
  fulfilment?: { bought: number; requested: number } | null
  as_of: string
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

export type DecisionOutcome = 'approved' | 'stopped' | 'uncertain'
export type UncertainOutcome = 'pending' | 'expired' | 'approved' | 'declined'

export interface EvidenceItem {
  rule: string
  outcome: 'pass' | 'fail' | 'uncertain' | 'info'
  detail: string
  source?: 'policy' | 'ledger' | 'history' | 'merchant_text' | 'model'
}

export interface DecisionItem {
  item_name: string
  quantity: number
  unit_price: number
  currency: string
  item_details: string
}

export type DecisionRelation = 'requote_of' | 'duplicate_of' | 'retry_of' | 'split_of'

export interface Decision {
  authorization_id: string
  customer_id: string
  card_id: string
  decision: DecisionOutcome
  uncertain_outcome: UncertainOutcome | null
  status: 'final' | 'pending_human'
  reason_codes: string[]
  message: string
  uncertainty: { note: string } | null
  occurred_at: string
  merchant: { merchant_id: string; name: string }
  amount: number
  currency: string
  billing_amount_chf: number
  items: DecisionItem[]
  injection_flag: { flagged: true; reason: string } | null
  evidence: EvidenceItem[]
  order_returnable: 'true' | 'false' | 'unknown' | 'not_applicable'
  delivery_by: string | null
  deadline_at?: string
  counterfactual?: string | null
  related?: { authorization_id: string; relation: DecisionRelation } | null
  session?: { trust: 'normal' | 'elevated' | 'frozen'; note: string } | null
  engine_version?: string
  latency_ms?: number
  explanation_source?: 'template' | 'model'
  resolved_by?: 'customer' | 'timeout'
  run_id?: string
  run_started_at?: string
}

// Operator-only shapes (docs/api-contract.md §1.2, §2 "Operator-only").

export interface ReplayStatus {
  scenario_id: string
  card_id: string
  delivered: number
  total: number
  running: boolean
  next_at: string | null
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
  customer_id?: string
  customer_name?: string
}

export interface ScenarioProfile {
  customer_id: string
  name: string
  card_id: string
  profile_id: string | null
  source: 'pack' | 'bootstrap' | 'run' | 'authorization'
}

export interface Scenario {
  scenario_id: string
  scenario_name: string
  cardholder_instruction: string
  served: boolean
  profile: ScenarioProfile | null
  active_run_id: string | null
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

export interface SoftSignalsState {
  live: boolean
  replay: boolean
}

export type CurrentRun = { kind: 'live'; run: LiveRun } | { kind: 'replay'; run: ReplayStatus }
