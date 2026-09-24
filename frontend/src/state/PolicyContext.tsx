import { createContext, useContext } from 'react'
import type { Mandate } from '../api/types'

export interface PolicyContextValue {
  // Keyed by card_id: what C3 last returned for each of the signed-in
  // customer's cards. A card missing here has no policy at all (C3 said
  // `null`); a revoked one stays, status 'revoked' — otherwise "never had a
  // policy" and "had one, revoked it" are indistinguishable when reviewing
  // past activity (D-044).
  policiesByCard: Record<string, Mandate>
  // 'loading' until the first C3 round for every card is back — until then
  // nothing may say a card has no policy. 'error' only when that first round
  // failed; a later failed refresh keeps what is already on screen.
  status: 'loading' | 'error' | 'ready'
  // Re-reads every card's policy from C3 and replaces the state above. The one
  // way policy state is loaded: on sign-in, after every decisions poll, after a
  // step-up answer (C8) and after confirm or revoke.
  refreshPolicies: () => Promise<void>
  retry: () => void
  // C2's response, shown at once, then confirmed by a refresh.
  setPolicyForCard: (cardId: string, mandate: Mandate) => void
  // After a C5 (204): shows the card revoked at once, then refreshes.
  revokePolicyForCard: (cardId: string) => void
}

export const PolicyContext = createContext<PolicyContextValue | null>(null)

export function usePolicy(): PolicyContextValue {
  const context = useContext(PolicyContext)
  if (!context) {
    throw new Error('usePolicy must be used within a PolicyProvider')
  }
  return context
}
