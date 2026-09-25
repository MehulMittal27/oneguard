/**
 * Copied from `frontend/src/lib/reasonCodes.ts` (root CLAUDE.md non-negotiable
 * 10: every decision is explained; the vocabulary is `docs/api-contract.md`
 * §4). Unlike the customer app this console may show a code the customer-facing
 * map does not yet have (development codes, a code just added to the
 * contract) — the fallback still holds, it just isn't customer wording.
 */
const REASON_LABEL: Record<string, string> = {
  within_limits: 'Within limits',
  rule_satisfied: 'Matched a rule',
  per_order_limit_exceeded: 'Over the per-order limit',
  period_limit_exceeded: 'Over the limit for this period',
  merchant_category_mismatch: 'Not an allowed merchant category',
  unfamiliar_merchant: 'Merchant not seen before',
  lookalike_merchant: 'Merchant name imitates a known one',
  item_mismatch: 'Not the requested item',
  unrequested_item: 'Item not requested was in the basket',
  return_terms_unknown: 'Return terms not stated',
  return_window_too_short: 'Return window shorter than allowed',
  duplicate_suspected: 'Looks like a repeat order',
  injection_suspected: "Merchant text tried to instruct the agent",
  new_device_burst: 'Burst of orders from a new device',
  card_or_authority_inactive: 'Card or policy not active',
  unevaluable: 'Rules could not be checked',
  customer_confirmation: 'Customer decided this one',
  split_order_suspected: 'Looks like one order split to dodge a limit',
  requote_accepted: 'Corrected price for an earlier decline',
  already_fulfilled: 'Requested item already bought',
  recurring_charge_added: 'Would start a repeating charge',
  wrong_size: 'Not the requested size',
  session_recovered: 'Normal activity resumed',
  on_other_card: 'Counted against a different card',
  foreign_currency_converted: 'Converted to CHF',
  ledger_mismatch: 'Ledger and platform totals disagree',
  period_reserved_pending: 'Limit partly held for a pending step-up',
  period_count_exceeded: 'More purchases than the period allows',
  shop_terms_contradictory: "Merchant's own terms contradict each other",
  no_purchase_history: 'No purchase history yet',
  session_watch: 'Double-checking after unusual activity',
  unusual_activity: 'Unusual activity on this card',
  rule_not_met: 'A rule was not met',
  stub: 'Stub decision (ONEGUARD_STUBS)',
}

const NEUTRAL_FALLBACK = 'Unmapped reason code'

export function reasonLabel(code: string): string {
  return REASON_LABEL[code] ?? NEUTRAL_FALLBACK
}
