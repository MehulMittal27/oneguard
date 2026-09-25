import type { ReactNode } from 'react'

type Tone = 'neutral' | 'approved' | 'stopped' | 'asked' | 'accent'

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-surface-sunken text-ink-muted',
  approved: 'bg-approved-tint text-approved',
  stopped: 'bg-stopped-tint text-stopped',
  asked: 'bg-asked-tint text-asked-ink',
  accent: 'bg-cord-accent-tint text-cord-accent',
}

/** One small pill, used for served/live/state/decision labels throughout the console. */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-pill px-3 py-1 text-[12px] font-semibold whitespace-nowrap ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  )
}
