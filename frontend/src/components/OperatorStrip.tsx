import { useEffect, useState } from 'react'
import { getReplayStatus, getSoftSignals, setSoftSignals } from '../api/operator'
import type { ReplayStatus, SoftSignalsState } from '../api/types'
import { DECISIONS_POLL_SECONDS } from '../config'
import { nextSoftSignals, softSignalsLabel } from '../lib/softSignals'

/**
 * P3-2's demo affordance: a thin operator strip behind `?demo=1`, showing the
 * current run's counters and a chaos toggle (D1, D5).
 *
 * Not a customer surface: opt-in by query param, above the app rather than in
 * any screen, and it shows engine plumbing the customer has no reason to see.
 *
 * Reads D1, not D4. D4 (`/api/dev/runs/{run_id}`) is the richer live view but
 * needs a `run_id` only D3 produces out of band, and who creates that run is
 * still open with P1. D1 needs no id, so the strip works today and gains the
 * live counters once that is settled.
 */
export function OperatorStrip() {
  const [replay, setReplay] = useState<ReplayStatus | null>(null)
  // Null until D5 answers: the strip never shows a state it has not read.
  const [signals, setSignals] = useState<SoftSignalsState | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let cancelled = false
    // D5 is read on every poll too, so the label follows a toggle made from
    // another tab or `make` target instead of keeping this tab's last press.
    const read = () => {
      Promise.all([getReplayStatus(), getSoftSignals()])
        .then(([status, state]) => {
          if (cancelled) return
          setReplay(status)
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
        <span>no replay yet</span>
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
