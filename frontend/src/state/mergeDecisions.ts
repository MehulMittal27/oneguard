import type { Decision } from '../api/types'

/**
 * Folds a freshly-read list of decisions into the ones already on screen.
 *
 * This is deliberately the *only* place incoming decisions become state, and
 * it knows nothing about how they arrived. Polling calls it today; an SSE
 * stream will call it with a single decision later, and a reconnect will call
 * it with a whole list again. Screens read the result and never learn which.
 *
 * The backend is authoritative for what a decision *is*. Two pieces of local
 * state survive a refresh anyway, and both matter:
 *
 * 1. **A running countdown.** `deadline_at` is real-clock, so taking the
 *    server's value on every poll would nudge the countdown and make it
 *    visibly stutter. The first value we saw is kept.
 * 2. **A locally expired step-up.** When the window passes unanswered the UI
 *    marks it expired without calling resolve — an unanswered step-up ends
 *    paused, not decided, and inventing a human answer is forbidden. The
 *    server still reports it as pending, so letting the server win would flip
 *    the row back to "waiting for you" and restart its countdown.
 */
export function mergeDecisions(current: Decision[], incoming: Decision[]): Decision[] {
  if (incoming.length === 0) return current

  const existingById = new Map(current.map((d) => [d.authorization_id, d]))
  let changed = false

  const merged = incoming.map((next) => {
    const existing = existingById.get(next.authorization_id)
    if (!existing) {
      changed = true
      return next
    }
    existingById.delete(next.authorization_id)

    const locallyExpired =
      existing.uncertain_outcome === 'expired' && next.status === 'pending_human'
    if (locallyExpired) return existing

    const reconciled: Decision = {
      ...next,
      deadline_at: existing.deadline_at ?? next.deadline_at,
    }
    if (!sameDecision(existing, reconciled)) {
      changed = true
      return reconciled
    }
    return existing
  })

  // Anything we hold that the server didn't return stays, rather than
  // disappearing from the customer's history mid-session.
  const orphans = [...existingById.values()]
  if (orphans.length > 0) changed = true

  return changed ? [...merged, ...orphans] : current
}

function sameDecision(a: Decision, b: Decision): boolean {
  return (
    a.decision === b.decision &&
    a.uncertain_outcome === b.uncertain_outcome &&
    a.status === b.status &&
    a.deadline_at === b.deadline_at &&
    a.message === b.message &&
    a.evidence.length === b.evidence.length
  )
}
