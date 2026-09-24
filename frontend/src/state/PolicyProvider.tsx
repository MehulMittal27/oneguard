import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { getAccounts } from '../api/accounts'
import { getPolicy } from '../api/policy'
import type { Mandate } from '../api/types'
import { useCustomer } from './CustomerContext'
import { PolicyContext, type PolicyContextValue } from './PolicyContext'

/**
 * Keyed by customer id in the wrapper below, like DecisionsProvider, so a
 * sign-out or a switch of customer starts from nothing instead of showing the
 * previous customer's policies.
 */
function PolicyProviderInner({
  customerId,
  children,
}: {
  customerId: string | null
  children: ReactNode
}) {
  const [policiesByCard, setPoliciesByCard] = useState<Record<string, Mandate>>({})
  const [status, setStatus] = useState<PolicyContextValue['status']>(
    customerId ? 'loading' : 'ready',
  )
  const [attempt, setAttempt] = useState(0)

  // The customer's cards, read once (C10) — they don't change during a session.
  const cardIds = useRef<string[] | null>(null)
  // Refreshes overlap (a poll every few seconds, plus one after each answer,
  // confirm and revoke), so each is numbered when it starts. A result is
  // applied only if nothing newer has been applied, and only if it started
  // after the last local change — an older read would put back the state that
  // change just replaced.
  const counter = useRef(0)
  const lastApplied = useRef(0)
  const lastLocalChange = useRef(0)
  const unmounted = useRef(false)

  const refreshPolicies = useCallback(async () => {
    if (!customerId) return
    const started = ++counter.current
    try {
      if (!cardIds.current) {
        const accounts = await getAccounts(customerId)
        cardIds.current = accounts.flatMap((a) => a.cards.map((c) => c.card_id))
      }
      const ids = cardIds.current
      const mandates = await Promise.all(ids.map((id) => getPolicy(id)))
      if (unmounted.current) return
      if (started < lastApplied.current || started < lastLocalChange.current) return
      lastApplied.current = started
      const next: Record<string, Mandate> = {}
      ids.forEach((id, i) => {
        const mandate = mandates[i]
        if (mandate) next[id] = mandate
      })
      setPoliciesByCard(next)
      setStatus('ready')
    } catch {
      if (unmounted.current) return
      // Same rule as the decisions feed: a failed refresh keeps what is on
      // screen. Only a failed first read leaves nothing to show.
      setStatus((current) => (current === 'ready' ? current : 'error'))
    }
  }, [customerId])

  useEffect(() => {
    unmounted.current = false
    refreshPolicies()
    return () => {
      unmounted.current = true
    }
  }, [refreshPolicies, attempt])

  function retry() {
    setStatus('loading')
    setAttempt((n) => n + 1)
  }

  function setPolicyForCard(cardId: string, mandate: Mandate) {
    lastLocalChange.current = ++counter.current
    setPoliciesByCard((prev) => ({ ...prev, [cardId]: mandate }))
    refreshPolicies()
  }

  function revokePolicyForCard(cardId: string) {
    lastLocalChange.current = ++counter.current
    setPoliciesByCard((prev) => {
      const mandate = prev[cardId]
      if (!mandate) return prev
      return { ...prev, [cardId]: { ...mandate, status: 'revoked' } }
    })
    refreshPolicies()
  }

  return (
    <PolicyContext.Provider
      value={{
        policiesByCard,
        status,
        refreshPolicies,
        retry,
        setPolicyForCard,
        revokePolicyForCard,
      }}
    >
      {children}
    </PolicyContext.Provider>
  )
}

export function PolicyProvider({ children }: { children: ReactNode }) {
  const { signedInAs } = useCustomer()
  const customerId = signedInAs?.customer_id ?? null
  return (
    <PolicyProviderInner key={customerId ?? 'signed-out'} customerId={customerId}>
      {children}
    </PolicyProviderInner>
  )
}
