/**
 * `occurred_at` is simulated scenario time (root CLAUDE.md Conventions):
 * shown in UTC, fixed regardless of the viewer's timezone, same as
 * frontend/src/lib/datetime.ts. `deadline_at` and run start times are the
 * real clock, so those render in the viewer's own timezone instead.
 */
export function formatSimTime(iso: string): string {
  const date = new Date(iso)
  const day = String(date.getUTCDate()).padStart(2, '0')
  const month = date.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })
  const time = `${String(date.getUTCHours()).padStart(2, '0')}:${String(date.getUTCMinutes()).padStart(2, '0')}`
  return `${day} ${month} · ${time} UTC`
}

export function formatRealTime(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
}
