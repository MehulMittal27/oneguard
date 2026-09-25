import type { Decision } from '../api/types'

/** One run's decisions, newest run first — same grouping idea as
 * `frontend/src/lib/runs.ts`, simplified: this console shows every run for a
 * card (not just the newest + "earlier"), because the whole point here is
 * comparing runs of the same scenario. */
export interface RunGroup {
  runId: string
  startedAt: string | null
  cardId: string
  decisions: Decision[]
}

export function groupByRun(decisions: Decision[]): { grouped: RunGroup[]; unattached: Decision[] } {
  const byRun = new Map<string, RunGroup>()
  const unattached: Decision[] = []
  for (const d of decisions) {
    if (!d.run_id) {
      unattached.push(d)
      continue
    }
    const existing = byRun.get(d.run_id)
    if (existing) {
      existing.decisions.push(d)
    } else {
      byRun.set(d.run_id, {
        runId: d.run_id,
        startedAt: d.run_started_at ?? null,
        cardId: d.card_id,
        decisions: [d],
      })
    }
  }
  const grouped = [...byRun.values()].sort((a, b) => (b.startedAt ?? '').localeCompare(a.startedAt ?? ''))
  return { grouped, unattached }
}
