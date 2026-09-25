import type { Customer } from '../api/types'

/** Offline `ReplayStatus` names a card but not a customer — resolve it client-side
 * from C12's list rather than adding a customer_id field the backend contract
 * does not have (root CLAUDE.md rule 3 is about engine code, but the same
 * instinct applies: don't invent a field, join by id on what's already there). */
export function customerForCard(cardId: string, customers: Customer[]): Customer | undefined {
  return customers.find((c) => c.card_id === cardId)
}
