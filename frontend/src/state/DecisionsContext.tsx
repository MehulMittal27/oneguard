import { createContext, useContext } from 'react'
import type { Decision } from '../api/types'

export interface DecisionsContextValue {
  // Every decision (final and pending alike) — Activity's feed and the
  // step-up inbox are both views over this one list, so resolving or
  // expiring a step-up shows up in both immediately.
  decisions: Decision[]
  pending: Decision[]
  status: 'loading' | 'error' | 'ready'
  resolve: (authorizationId: string, decision: 'approve' | 'decline') => Promise<void>
  retry: () => void
}

export const DecisionsContext = createContext<DecisionsContextValue | null>(null)

export function useDecisions(): DecisionsContextValue {
  const context = useContext(DecisionsContext)
  if (!context) {
    throw new Error('useDecisions must be used within a DecisionsProvider')
  }
  return context
}
