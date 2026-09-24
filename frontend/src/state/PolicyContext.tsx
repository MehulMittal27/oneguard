import { createContext, useContext } from 'react'
import type { Mandate } from '../api/types'

export interface PolicyContextValue {
  // Keyed by card_id. Session-only (no backend to persist to yet) — mirrors
  // CustomerContext's durability, see frontend/.claude/CLAUDE.md.
  policiesByCard: Record<string, Mandate>
  setPolicyForCard: (cardId: string, mandate: Mandate) => void
  // Revoke keeps the mandate (status flips to 'revoked') instead of
  // deleting it — otherwise "never had a policy" and "had one, revoked
  // it" are indistinguishable when reviewing past activity (D-044).
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
