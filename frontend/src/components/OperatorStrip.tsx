import { useEffect, useState } from 'react'
import { getCurrentRun, setSoftSignals } from '../api/operator'
import type { LiveRun } from '../api/types'
import { DECISIONS_POLL_SECONDS } from '../config'

/**
 * P3-2's demo affordance: a thin operator strip behind `?demo=1`, showing the
 * current run's counters and a chaos toggle (D5, D7).
 *
 * Not a customer surface: opt-in by query param, above the app rather than in
 * any screen, and it shows engine plumbing the customer has no reason to see.
 *
 * Reads D7 (`/api/dev/runs/current`), which avoids making the operator supply a
 * run id and exposes the worker's decided and pending counters directly.
 */
export function OperatorStrip() {
  const [run, setRun] = useState<LiveRun | null>(null)
  const [signalsOn, setSignalsOn] = useState(true)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let cancelled = false
    const read = () => {
      getCurrentRun()
        .then((currentRun) => {
          if (cancelled) return
          setRun(currentRun)
          setUnreachable(false)
        })
        .catch(() => {
          if (cancelled) return
          setUnreachable(true)
        })
    }
    read()
    const id = setInterval(read, DECISIONS_POLL_SECONDS * 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function toggleSignals() {
    const next = !signalsOn
    // Optimistic, then corrected by what the backend reports it actually did —
    // a toggle that lies about engine state is worse than one that lags.
    setSignalsOn(next)
    try {
      setSignalsOn(await setSoftSignals(next))
    } catch {
      setSignalsOn(!next)
      setUnreachable(true)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 bg-ink px-4 py-2 text-[11px] text-on-ink-muted">
      <span className="font-semibold tracking-[0.08em] text-on-ink uppercase">Operator</span>

      {unreachable ? (
        <span>backend unreachable</span>
      ) : run ? (
        <>
          <span className="tabular-nums">
            {run.scenario_id} · {run.card_id}
          </span>
          <span className="tabular-nums">
            {run.decided}/{run.total} decided · {run.pending_human} pending
          </span>
          <span>{run.state}</span>
        </>
      ) : (
        <span>no run</span>
      )}

      <button
        type="button"
        onClick={toggleSignals}
        aria-pressed={signalsOn}
        className="ml-auto min-h-8 rounded-pill border border-on-ink-rule px-3 py-1 font-semibold text-on-ink"
      >
        Soft signals: {signalsOn ? 'on' : 'off'}
      </button>
    </div>
  )
}
