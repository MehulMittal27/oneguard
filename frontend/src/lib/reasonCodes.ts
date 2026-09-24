/**
 * The one code-to-text map (`.claude/CLAUDE.md` hard rule 9). Every screen that
 * shows a reason code reads it from here, so a code is never worded two ways.
 *
 * The vocabulary is `../docs/api-contract.md` §4, which is the contract's, not
 * this file's: a code is added there before it is emitted. Labels are written
 * for the customer, not the engine — they say what happened to their money, and
 * never name a rule id, a field or a table.
 *
 * An unknown code falls back to one neutral line rather than a humanised version
 * of the code itself. Showing `wrong_size` as "Wrong size" would be friendlier
 * right up to the first code whose id is not something a customer should read.
 */
const REASON_LABEL: Record<string, string> = {
  // §4 "Existing"
  within_limits: 'Within your limits',
  rule_satisfied: 'Matched your rules',
  per_order_limit_exceeded: 'Over your per-order limit',
  period_limit_exceeded: 'Over your limit for this period',
  merchant_category_mismatch: "Not the kind of shop you allowed",
  unfamiliar_merchant: "A shop you haven't used before",
  lookalike_merchant: "A shop whose name imitates one you use",
  item_mismatch: "Not the item you asked for",
  unrequested_item: 'Something you did not ask for was in the basket',
  return_terms_unknown: "The shop didn't state its return terms",
  return_window_too_short: 'The return window is shorter than you allow',
  duplicate_suspected: 'Looks like a repeat of an order you already placed',
  injection_suspected: "The shop's text tried to instruct your agent",
  new_device_burst: 'Several orders from a device new to this card',
  card_or_authority_inactive: 'This card or policy is not active',
  unevaluable: "Your rules couldn't be checked against this purchase",
  customer_confirmation: 'You decided this one',

  // §4 "Added"
  split_order_suspected: 'Looks like one order split to stay under your limit',
  requote_accepted: 'A corrected price for an order stopped earlier',
  already_fulfilled: 'What you asked for has already been bought',
  recurring_charge_added: 'This would start a repeating charge',
  wrong_size: 'Not the size you asked for',
  session_recovered: 'Normal activity resumed on this card',
  on_other_card: 'Counted against a different card',
  foreign_currency_converted: 'Converted to Swiss francs',
  ledger_mismatch: 'Our running total and the platform’s disagree',
  period_reserved_pending: 'Part of your limit is held for a purchase awaiting your answer',
  period_count_exceeded: 'More orders than you allowed for this period',
  shop_terms_contradictory: "The shop's own terms contradict each other",
  no_purchase_history: 'You have no purchase history yet',

  // Session and timing. `session_watch` is the engine watching a card after
  // unusual activity: the wording is the same sentence the session banner uses,
  // so the customer reads one explanation, not two.
  session_watch: "We're double-checking after unusual activity on your card",
  unusual_activity: 'Unusual activity on this card',
  rule_not_met: "One of your rules wasn't met",
  session_risk: 'This session does not look like you',
  velocity_burst: 'Several orders in a very short time',
  night_purchase: 'Placed outside this card’s usual hours',

  // Development only (`../docs/api-contract.md` §4) — never emitted in a live
  // run, but labelled so a stubbed backend does not render as "another check".
  stub: 'Stub decision — the engine is running on stubs',
}

const NEUTRAL_FALLBACK = 'Another check your rules ran'

/** The customer-facing label for a reason code; never throws, never blank. */
export function reasonLabel(code: string): string {
  return REASON_LABEL[code] ?? NEUTRAL_FALLBACK
}

/** True when the code is one this build knows how to word. */
export function isKnownReasonCode(code: string): boolean {
  return code in REASON_LABEL
}
