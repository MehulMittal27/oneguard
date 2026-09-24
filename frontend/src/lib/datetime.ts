const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

/**
 * DESIGN.md time convention: short day labels ("Fri 14 Aug"). UTC, not
 * local time — `occurred_at` is simulated scenario time, fixed regardless
 * of the viewer's timezone.
 */
export function formatShortDate(iso: string): string {
  const date = new Date(iso)
  return `${DAY_NAMES[date.getUTCDay()]} ${date.getUTCDate()} ${MONTH_NAMES[date.getUTCMonth()]}`
}

/** Same-day grouping key, independent of display formatting. */
export function dateKey(iso: string): string {
  return iso.slice(0, 10)
}

/** DESIGN.md time convention: 24h ("10:05"). UTC, same reasoning as formatShortDate. */
export function formatTime(iso: string): string {
  const date = new Date(iso)
  return `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`
}
