import { useEffect, useState } from 'react'
import { getReplayStatus, setSoftSignals } from '../api/operator'
import type { ReplayStatus } from '../api/types'
import { DECISIONS_POLL_SECONDS } from '../config'

/**
 * P3-2's demo affordance: a thin operator strip behind `?demo=1`, showing the
 * current run's counters and a chaos toggle (D1, D5).
 *
 * Not a customer surface: opt-in by query param, above the app rather than in
 * any screen, and it shows engine plumbing the customer has no reason to see.
 *
 * Reads D1, not D4. D4 (`/api/dev/runs/{run_id}`) is the richer live view but
 * needs a `run_id` only D3 produces out of band (`TASKS.md` Q5). D1 needs none,
 * so the strip works today and gains live counters once Q5 is settled.
 */
export function OperatorStrip() {
  const [replay, setReplay] = useState<ReplayStatus | null>(null)
  const [signalsOn, setSignalsOn] = useState(true)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let cancelled = false
    const read = () => {
      getReplayStatus()
        .then((status) => {
          if (cancelled) return
          setReplay(status)
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
      ) : replay ? (
        <>
          <span className="tabular-nums">
            {replay.scenario_id} · {replay.card_id}
          </span>
          <span className="tabular-nums">
            {replay.delivered}/{replay.total} delivered
          </span>
          <span>{replay.running ? 'running' : 'idle'}</span>
        </>
      ) : (
        <span>no replay running</span>
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
