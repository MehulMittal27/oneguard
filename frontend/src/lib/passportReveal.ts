import type { Mandate } from '../api/types'

/**
 * When the first-passport animation plays (`../../docs/passport.md`): only
 * right after C2 issued a card's very first passport — the confirm response
 * carries `passport.version === 1` and the card had no passport before (its
 * previous policy, active or revoked, carried none). A new policy on a card
 * that already had a passport also starts at version 1 of its own passport,
 * so the version alone is not enough. Later versions never animate.
 *
 * The confirmation screen and the card's Passport section each play it once
 * for that passport, and only within `REVEAL_WINDOW_MS` of the confirm, so a
 * card opened much later just shows the static passport.
 */

export const REVEAL_WINDOW_MS = 60_000
/** Everything in the animation has settled by then; the card is static after. */
export const REVEAL_MS = 800

export type RevealSurface = 'confirmation' | 'card'

export function isFirstPassport(before: Mandate | null | undefined, confirmed: Mandate): boolean {
  return confirmed.passport?.version === 1 && !before?.passport
}

interface Reveal {
  passportId: string
  at: number
  played: Set<RevealSurface>
}

const reveals = new Map<string, Reveal>()

/** Records a card's first passport, just issued (call after C2 when `isFirstPassport`). */
export function markReveal(cardId: string, passportId: string, now: number = Date.now()): void {
  reveals.set(cardId, { passportId, at: now, played: new Set() })
}

/**
 * True exactly once per surface for the passport `markReveal` recorded on this
 * card, within the window; false for any other passport, surface replay or late read.
 */
export function takeReveal(
  cardId: string,
  passportId: string,
  surface: RevealSurface,
  now: number = Date.now(),
): boolean {
  const reveal = reveals.get(cardId)
  if (!reveal || reveal.passportId !== passportId || reveal.played.has(surface)) return false
  if (now - reveal.at > REVEAL_WINDOW_MS) {
    reveals.delete(cardId)
    return false
  }
  reveal.played.add(surface)
  return true
}

/** The viewer asked for less motion: then nothing animates. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
}
