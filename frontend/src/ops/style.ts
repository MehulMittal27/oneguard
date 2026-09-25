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

// Buttons in Viseca's gold (primary) and outline (secondary), `index.css` `.ops-viseca`.
const BUTTON = `${TEXT_M} inline-flex min-h-10 items-center justify-center gap-2 rounded-button px-5 font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40`
export const BUTTON_PRIMARY = `${BUTTON} bg-[var(--ops-gold)] text-ink enabled:hover:bg-[var(--ops-gold-hover)]`
export const BUTTON_SECONDARY = `${BUTTON} border border-border-quiet bg-surface text-ink enabled:hover:border-[var(--ops-gold)]`
export const LINK = 'font-semibold text-[var(--ops-link)] underline decoration-[var(--ops-link)]/40 underline-offset-4 hover:decoration-[var(--ops-link)]'
