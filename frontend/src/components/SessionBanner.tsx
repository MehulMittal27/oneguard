import type { Decision } from '../api/types'

const SESSION_STYLE: Record<'elevated' | 'frozen', { label: string; box: string; fg: string }> = {
  elevated: {
    label: 'Session under watch',
    box: 'border-asked-border bg-asked-tint',
    fg: 'text-asked-ink',
  },
  frozen: {
    label: 'Session paused',
    box: 'border-stopped-border bg-stopped-tint',
    fg: 'text-stopped',
  },
}

/**
 * Session freeze is engine state, not a mandate change (docs/api-contract.md
 * §3): after a burst the engine watches the session, then relaxes. It is shown
 * wherever a decision is, so the customer sees the same reason on the step-up
 * they are answering as on the decision they are reading.
 *
 * Renders nothing at `normal` or when the backend sent no session at all —
 * absent is not "fine", it is "not said", and neither deserves a banner.
 */
export function SessionBanner({ session }: { session: Decision['session'] }) {
  if (!session || session.trust === 'normal') return null

  const style = SESSION_STYLE[session.trust]
  // The note is the engine's own words. A frozen session gets a plain-language
  // fallback so the customer is never left with an empty reason.
  const note =
    session.note || "We're double-checking after unusual activity on your card"

  return (
    <div className={`rounded-row border px-4 py-3 ${style.box}`}>
      <p className={`text-[11px] font-semibold tracking-[0.08em] uppercase ${style.fg}`}>
        {style.label}
      </p>
      <p className={`mt-1 text-[13px] ${style.fg}`}>{note}</p>
    </div>
  )
}
