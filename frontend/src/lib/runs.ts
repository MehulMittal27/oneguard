import type { Decision } from '../api/types'

/** One older run on a card, folded under "Earlier runs". */
export interface EarlierRun {
  key: string
  cardId: string
  // Real clock; null when the backend sent no start for the run.
  startedAt: string | null
  decisions: Decision[]
}

export interface RunSplit {
  // Each card's newest run, plus every decision that names no run.
  current: Decision[]
  // Every other run, newest start first.
  earlier: EarlierRun[]
}

/**
 * Splits decisions into each card's newest run and its earlier runs
 * (`../../docs/api-contract.md` §6 item 14). Replaying a scenario on a card
 * starts a new run with the same purchases, so listing every run together reads
 * as duplicates.
 *
 * The newest run on a card is the one with the latest `run_started_at`; a run
 * without a start sorts oldest. A decision without `run_id` is never hidden:
 * it stays in `current`. Input order is kept inside each group.
 */
export function splitByRun(decisions: Decision[]): RunSplit {
  const newestByCard = new Map<string, { runId: string; startedAt: string }>()
  for (const d of decisions) {
    if (!d.run_id) continue
    const startedAt = d.run_started_at ?? ''
    const newest = newestByCard.get(d.card_id)
    if (!newest || startedAt > newest.startedAt) {
      newestByCard.set(d.card_id, { runId: d.run_id, startedAt })
    }
  }

  const current: Decision[] = []
  const earlierByKey = new Map<string, EarlierRun>()
  for (const d of decisions) {
    if (!d.run_id || newestByCard.get(d.card_id)?.runId === d.run_id) {
      current.push(d)
      continue
    }
    const key = `${d.card_id}/${d.run_id}`
    const run = earlierByKey.get(key)
    if (run) {
      run.decisions.push(d)
    } else {
      earlierByKey.set(key, {
        key,
        cardId: d.card_id,
        startedAt: d.run_started_at ?? null,
        decisions: [d],
      })
    }
  }

  const earlier = [...earlierByKey.values()].sort((a, b) =>
    (b.startedAt ?? '').localeCompare(a.startedAt ?? ''),
  )
  return { current, earlier }
}

export interface DecisionCounts {
  all: number
  approved: number
  stopped: number
  uncertain: number
}

/**
 * The counts Activity's filter chips and Home's `OverviewHero` both show, taken
 * from the same list (`splitByRun(...).current`) so the two can never disagree.
 * No time window: a run spanning more simulated days than any window would drop
 * its early purchases from one screen and not the other.
 */
export function countDecisions(decisions: Decision[]): DecisionCounts {
  return {
    all: decisions.length,
    approved: decisions.filter((d) => d.decision === 'approved').length,
    stopped: decisions.filter((d) => d.decision === 'stopped').length,
    uncertain: decisions.filter((d) => d.decision === 'uncertain').length,
  }
}

/**
 * A run's start as "Thu 24 Sep · 14:03". Unlike `occurred_at`, which is
 * simulated time shown in UTC (`datetime.ts`), a run starts on the real clock,
 * so it reads in the viewer's own time zone.
 */
export function formatRunStart(iso: string, timeZone?: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZone,
    })
      .formatToParts(new Date(iso))
      .map((part) => [part.type, part.value]),
  )
  return `${parts.weekday} ${parts.day} ${parts.month} · ${parts.hour}:${parts.minute}`
}
