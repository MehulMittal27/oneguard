/** Same convention as frontend/src/lib/money.ts: "CHF 126.00". */
export function formatChf(amount: number): string {
  return `CHF ${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
