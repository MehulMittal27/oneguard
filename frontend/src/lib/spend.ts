import type { Decision, Mandate, RuleCheck } from '../api/types'

const PER_ORDER_PATTERN = /total at or below chf\s*([\d,]+(?:\.\d+)?)\s*per order/i
const PERIOD_PATTERN = /total at or below chf\s*([\d,]+(?:\.\d+)?)\s*across any (\d+) days/i

export interface PolicyLimits {
  perOrder: number | null
  period: { limitChf: number; days: number } | null
}

/** Reads the customer's own chosen limits back out of a mandate's plain-language checks. */
export function parseLimits(checks: RuleCheck[]): PolicyLimits {
  let perOrder: number | null = null
  let period: PolicyLimits['period'] = null
  for (const check of checks) {
    const perOrderMatch = check.text.match(PER_ORDER_PATTERN)
    if (perOrderMatch) perOrder = Number(perOrderMatch[1].replace(/,/g, ''))
    const periodMatch = check.text.match(PERIOD_PATTERN)
    if (periodMatch) {
      period = { limitChf: Number(periodMatch[1].replace(/,/g, '')), days: Number(periodMatch[2]) }
    }
  }
  return { perOrder, period }
}

/**
 * The mandate's limits: the engine ledger's `usage` when the backend sends
 * it (docs/api-contract.md §2, authoritative), otherwise read back out of
 * the check wording (§3.9).
 */
export function limitsFromMandate(mandate: Mandate): PolicyLimits {
  const { usage } = mandate
  if (!usage) return parseLimits(mandate.checks)
  return {
    perOrder: usage.per_order_limit_chf,
    period:
      usage.period_limit_chf !== null && usage.period_days !== null
        ? { limitChf: usage.period_limit_chf, days: usage.period_days }
        : null,
  }
}

/** Final approvals only: approved by the rules, or approved by the customer after a step-up. */
function isSpend(d: Decision): boolean {
  return (
    d.decision === 'approved' || (d.decision === 'uncertain' && d.uncertain_outcome === 'approved')
  )
}

/**
 * Spend so far in a card's rolling period window (docs/rules.md M4:
 * "Only final approvals are spend", including a step-up the customer
 * approved). The window
 * ends at the most recent approved purchase's own simulated timestamp, not
 * the real clock — these are demo/historical dates, not "today".
 */
export function computePeriodSpend(decisions: Decision[], cardId: string, days: number): number {
  const approved = decisions.filter((d) => d.card_id === cardId && isSpend(d))
  if (approved.length === 0) return 0
  const windowEnd = approved.reduce(
    (latest, d) => (d.occurred_at > latest ? d.occurred_at : latest),
    approved[0].occurred_at,
  )
  const windowStartMs = new Date(windowEnd).getTime() - days * 24 * 60 * 60 * 1000
  return approved
    .filter((d) => new Date(d.occurred_at).getTime() > windowStartMs)
    .reduce((sum, d) => sum + d.billing_amount_chf, 0)
}

export interface PolicySpend {
  spentChf: number
  pendingChf: number
  /** 'ledger' when the engine sent `usage`; 'client' is the mock-mode fallback. */
  source: 'ledger' | 'client'
}

/**
 * What the meter reads. The engine ledger's `usage` is authoritative whenever the
 * backend sends it (docs/api-contract.md §2): `period_spent_chf` is final
 * approvals only, including step-ups the customer approved, and `pending_chf` is
 * what is stepped up and waiting — a reservation, never spend.
 *
 * The client-side computation below is the mock-mode fallback only. It cannot
 * see redelivery, re-quotes or a frozen session, so where the two ever disagree
 * the ledger is right.
 */
export function spendFromMandate(
  mandate: Mandate | undefined,
  decisions: Decision[],
  cardId: string,
  days: number,
): PolicySpend {
  const usage = mandate?.usage
  if (usage) {
    return {
      spentChf: usage.period_spent_chf,
      pendingChf: usage.pending_chf,
      source: 'ledger',
    }
  }
  return {
    spentChf: computePeriodSpend(decisions, cardId, days),
    pendingChf: computePendingChf(decisions, cardId),
    source: 'client',
  }
}

/** The dashed "ghost" preview: what the meter would read if pending purchases on this card were approved. */
export function computePendingChf(decisions: Decision[], cardId: string): number {
  return decisions
    .filter((d) => d.card_id === cardId && d.status === 'pending_human')
    .reduce((sum, d) => sum + d.billing_amount_chf, 0)
}

/** What a card's row under "Active policies" on Home says about its use. */
export interface PolicyUse {
  /** Final approvals (a customer-approved step-up included) in the card's newest run. */
  approved: number
  /** The ledger's spend (`usage`): the period window with a period limit, else the whole newest run. */
  spentChf: number
  /** Stepped up and waiting: a reservation, never spend. */
  pendingChf: number
  period: { limitChf: number; days: number } | null
}

/**
 * The use of a card's policy for Home, re-derived on every poll: the counts from
 * the same newest-run decisions Activity lists, the money from the mandate's
 * `usage`, which C3 re-reads after each decisions poll. A policy with no period
 * limit still moves: its spend is the newest run's. Without `usage` (mock mode)
 * the money is summed from those decisions instead.
 */
export function policyUse(mandate: Mandate, current: Decision[], cardId: string): PolicyUse {
  const spend = current.filter((d) => d.card_id === cardId && isSpend(d))
  const { period } = limitsFromMandate(mandate)
  const usage = mandate.usage
  const counted = period ? inPeriod(spend, period.days, usage) : spend
  return {
    approved: counted.length,
    spentChf: usage
      ? usage.period_spent_chf
      : counted.reduce((sum, d) => sum + d.billing_amount_chf, 0),
    pendingChf: usage ? usage.pending_chf : computePendingChf(current, cardId),
    period,
  }
}

/**
 * The purchases inside the period window the spend is measured over: the
 * ledger's (`usage.period_window_start` to `as_of`) when sent, otherwise
 * `computePeriodSpend`'s, so the count and the amount always cover the same days.
 */
function inPeriod(spend: Decision[], days: number, usage: Mandate['usage']): Decision[] {
  const ms = (iso: string) => new Date(iso).getTime()
  if (usage) {
    const start = ms(usage.period_window_start)
    const end = ms(usage.as_of)
    return spend.filter((d) => ms(d.occurred_at) >= start && ms(d.occurred_at) <= end)
  }
  if (spend.length === 0) return []
  const end = Math.max(...spend.map((d) => ms(d.occurred_at)))
  return spend.filter((d) => ms(d.occurred_at) > end - days * 24 * 60 * 60 * 1000)
}
