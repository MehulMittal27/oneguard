/**
 * A read that belongs to one card (its passport, its devices, this browser's
 * device on it, a receipt's check): held together with the card it was read for,
 * so a screen that moves to another card never shows the previous card's answer.
 *
 * - Switching cards clears synchronously: `viewFor` shows nothing held for another
 *   card, so the new card starts at `loading`, before any request goes out.
 * - A late answer for a card the screen has left is dropped (`settle`).
 * - A failed re-read keeps what the same card already showed (a poll hiccup);
 *   a failed first read for a new card is an error, never the old card's data.
 */
export type CardRead<T> = { cardId: string; status: 'ready'; value: T } | { cardId: string; status: 'error' }

export type CardView<T> = { status: 'loading' } | { status: 'error' } | { status: 'ready'; value: T }

/** What the screen for `cardId` shows from what is held. */
export function viewFor<T>(held: CardRead<T> | null, cardId: string): CardView<T> {
  if (!held || held.cardId !== cardId) return { status: 'loading' }
  return held.status === 'ready' ? { status: 'ready', value: held.value } : { status: 'error' }
}

/** What to hold once `arrived` comes back while the screen shows `current`. */
export function settle<T>(held: CardRead<T> | null, arrived: CardRead<T>, current: string): CardRead<T> | null {
  if (arrived.cardId !== current) return held
  if (arrived.status === 'error' && held?.cardId === current && held.status === 'ready') return held
  return arrived
}
