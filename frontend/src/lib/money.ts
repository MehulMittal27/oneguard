/** DESIGN.md money convention: "CHF 126.00" — space, 2 decimals, comma thousands. */
export function formatChf(amount: number): string {
  return `CHF ${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}
