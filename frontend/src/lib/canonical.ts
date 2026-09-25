/**
 * Canonical JSON — the exact bytes a device signs (`../../docs/passport.md`
 * §2.1, the backend's `passport/canonical.py`): object keys sorted, no
 * whitespace, a number under a money key (`billing_amount_chf`, `amount`,
 * `unit_price`) as a two-decimal string rounded half-even, every other number
 * as `JSON.stringify` writes it (so `400`, never `400.0`), `undefined` object
 * members left out as `JSON.stringify` leaves them out.
 *
 * Objects are written key by key rather than through `JSON.stringify(obj)`,
 * which would put integer-like keys ("10", "2") first whatever the sort.
 */

const MONEY_KEYS = new Set(['billing_amount_chf', 'amount', 'unit_price'])

/** An amount as documents carry it: two decimals, half-even on the cent. */
export function moneyString(value: number): string {
  const cents = value * 100
  const floor = Math.floor(cents)
  const tie = Math.abs(cents - floor - 0.5) < 1e-9
  const rounded = tie ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(cents)
  return (rounded / 100).toFixed(2)
}

function write(value: unknown, key: string | null): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return `[${value.map((item) => write(item ?? null, key)).join(',')}]`
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${write(v, k)}`).join(',')}}`
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('documents carry finite numbers only')
    return key !== null && MONEY_KEYS.has(key) ? JSON.stringify(moneyString(value)) : JSON.stringify(value)
  }
  return JSON.stringify(value)
}

/** The canonical string of `value`; the same value always gives the same string. */
export function canonical(value: unknown): string {
  return write(value, null)
}
