import { useEffect, useState } from 'react'
import { getCurrentRun, getSoftSignals, setSoftSignals, type CurrentRun } from '../api/operator'
import type { SoftSignalsState } from '../api/types'
import { DECISIONS_POLL_SECONDS } from '../config'
import { nextSoftSignals, softSignalsLabel } from '../lib/softSignals'

/**
 * P3-2's demo affordance: a thin operator strip behind `?demo=1`, showing the
 * current run's counters and a chaos toggle (D5, D7).
 *
 * Not a customer surface: opt-in by query param, above the app rather than in
 * any screen, and it shows engine plumbing the customer has no reason to see.
 *
 * Reads D7 (`/api/dev/runs/current`), which avoids making the operator supply a
 * run id. A live run shows the worker's decided and pending counters; an offline
 * replay (D2) only has D1's delivered counter, so that is what it shows.
 */
export function OperatorStrip() {
  const [run, setRun] = useState<CurrentRun | null>(null)
  // Null until D5 answers: the strip never shows a state it has not read.
  const [signals, setSignals] = useState<SoftSignalsState | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let cancelled = false
    // D5 is read on every poll too, so the label follows a toggle made from
    // another tab or `make` target instead of keeping this tab's last press.
    const read = () => {
      Promise.all([getCurrentRun(), getSoftSignals()])
        .then(([currentRun, state]) => {
          if (cancelled) return
          setRun(currentRun)
          setSignals(state)
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
    const previous = signals
    const next = nextSoftSignals(signals)
    // Optimistic, then corrected by what the backend reports it actually did:
    // a toggle that lies about engine state is worse than one that lags. D5
    // switches live and replay together.
    setSignals({ live: next, replay: next })
    try {
      const enabled = await setSoftSignals(next)
      setSignals({ live: enabled, replay: enabled })
    } catch {
      setSignals(previous)
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
            {run.run.scenario_id} · {run.run.card_id}
          </span>
          {run.kind === 'live' ? (
            <>
              <span className="tabular-nums">
                {run.run.decided}/{run.run.total} decided · {run.run.pending_human} pending
              </span>
              <span>{run.run.state}</span>
            </>
          ) : (
            <>
              <span className="tabular-nums">
                {run.run.delivered}/{run.run.total} delivered
              </span>
              <span>replay {run.run.running ? 'running' : 'idle'}</span>
            </>
          )}
        </>
      ) : (
        <span>no run yet</span>
      )}

      <button
        type="button"
        onClick={toggleSignals}
        disabled={!signals}
        aria-pressed={Boolean(signals?.live && signals.replay)}
        className="ml-auto min-h-8 rounded-pill border border-on-ink-rule px-3 py-1 font-semibold text-on-ink"
      >
        Soft signals: {softSignalsLabel(signals)}
      </button>
    </div>
  )
}
