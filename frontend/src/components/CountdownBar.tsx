import { useEffect, useState } from 'react'
import { HUMAN_WINDOW_SECONDS } from '../config'

function formatRemaining(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

/**
 * DESIGN.md's pending-state countdown: the fill represents time
 * *remaining* (starts full, drains from the right toward the left as the
 * 120s human window runs out), plus mm:ss, in countdown-fill/countdown-track
 * (cord-accent violet — a deliberately separate token from asked/asked-track,
 * since this is a ticking-clock indicator, not the "uncertain" decision
 * color). Only this — never any other on-screen time — uses the real clock
 * (frontend/.claude/CLAUDE.md Conventions), ticking independently of
 * DecisionsProvider so the bar animates smoothly without re-rendering the
 * whole pending list.
 */
export function CountdownBar({ deadlineAt }: { deadlineAt: string }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  const totalMs = HUMAN_WINDOW_SECONDS * 1000
  const remainingMs = Math.max(0, new Date(deadlineAt).getTime() - now)
  const remainingFraction = Math.min(1, Math.max(0, remainingMs / totalMs))

  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 flex-1 overflow-hidden rounded-pill bg-countdown-track">
        <div
          className="h-full rounded-pill bg-countdown-fill transition-[width]"
          style={{ width: `${remainingFraction * 100}%` }}
        />
      </div>
      <span className="shrink-0 text-[13px] font-semibold text-cord-accent tabular-nums">
        {formatRemaining(remainingMs)}
      </span>
    </div>
  )
}
