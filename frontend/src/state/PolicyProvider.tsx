import { useState, type ReactNode } from 'react'
import type { Mandate } from '../api/types'
import { PolicyContext } from './PolicyContext'

export function PolicyProvider({ children }: { children: ReactNode }) {
  const [policiesByCard, setPoliciesByCard] = useState<Record<string, Mandate>>({})

  function setPolicyForCard(cardId: string, mandate: Mandate) {
    setPoliciesByCard((prev) => ({ ...prev, [cardId]: mandate }))
  }

  function revokePolicyForCard(cardId: string) {
    setPoliciesByCard((prev) => {
      const mandate = prev[cardId]
      if (!mandate) return prev
      return { ...prev, [cardId]: { ...mandate, status: 'revoked' } }
    })
  }

  return (
    <PolicyContext.Provider value={{ policiesByCard, setPolicyForCard, revokePolicyForCard }}>
      {children}
    </PolicyContext.Provider>
  )
}
