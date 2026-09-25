import { api } from './client'
import type { LedgerSnapshot } from './types'

/** D6: the ledger of the card's latest run, as the engine counts it. */
export function getLedger(cardId: string): Promise<LedgerSnapshot> {
  return api.get<LedgerSnapshot>(`/dev/ledger/${encodeURIComponent(cardId)}`)
}
