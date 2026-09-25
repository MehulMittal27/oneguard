/**
 * The console's three text sizes, and no others: large for the instruction and
 * the decision rows (read from across a room), small for chips and the passport
 * line, medium for everything else, which mostly sits behind an expand.
 */
export const TEXT_L = 'text-[20px] leading-[1.35]'
export const TEXT_M = 'text-[14px] leading-[1.45]'
export const TEXT_S = 'text-[12px] leading-[1.35]'

/** Chip and badge colours by tone; the words and icons carry the meaning too. */
export const TONE_CLASS = {
  ok: 'bg-approved-tint text-approved',
  approved: 'bg-approved-tint text-approved',
  warn: 'bg-asked-tint text-asked-ink',
  asked: 'bg-asked-tint text-asked-ink',
  bad: 'bg-stopped-tint text-stopped',
  stopped: 'bg-stopped-tint text-stopped',
  neutral: 'bg-surface-sunken text-ink-muted',
  muted: 'bg-surface-expired text-ink-muted',
} as const
