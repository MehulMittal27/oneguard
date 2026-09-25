import { useEffect, useState, type ReactNode } from 'react'
import { getDecisions } from '../api/decisions'
import { resolveApproval } from '../api/approvals'
import { useDevice } from './DeviceContext'
import type { Decision } from '../api/types'
import { DECISIONS_POLL_SECONDS } from '../config'
import { useCustomer } from './CustomerContext'
import { usePolicy } from './PolicyContext'
import { DecisionsContext, type DecisionsContextValue } from './DecisionsContext'
import { mergeDecisions } from './mergeDecisions'

/**
 * Keyed by customer id in the wrapper below so switching the signed-in
 * customer remounts this with fresh state, instead of resetting state
 * inside an effect (avoids cascading synchronous setState-in-effect calls).
 */
function DecisionsProviderInner({
  customerId,
  children,
}: {
  customerId: string | null
  children: ReactNode
}) {
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [status, setStatus] = useState<DecisionsContextValue['status']>(
    customerId ? 'loading' : 'ready',
  )
  const [attempt, setAttempt] = useState(0)
  const { refreshPolicies } = usePolicy()
  const { withDevice } = useDevice()

  // Read once on sign-in, then kept current on a timer: the agent proposes
  // purchases while the customer has the app open, so decisions arrive during
  // a session rather than only before one.
  //
  // Every read goes through `mergeDecisions`, which is what keeps a repeated
  // fetch from disturbing a running countdown or un-expiring a step-up. That
  // one function is also the whole seam for swapping polling out for a live
  // stream later — no screen is involved either way.
  //
  // Each read is followed by a policy refresh (C3): a new decision moves the
  // ledger, and the meter reads the ledger's `usage`, not a sum of its own.
  useEffect(() => {
    if (!customerId) return
    let cancelled = false

    async function read(isFirst: boolean) {
      try {
        const result = await getDecisions(customerId!)
        if (cancelled) return
        setDecisions((current) => mergeDecisions(current, result))
        setStatus('ready')
      } catch {
        if (cancelled) return
        // A failed refresh keeps whatever is already on screen: the decisions
        // shown are still true, they are just no longer fresh. Only a failed
        // *first* read leaves nothing to show, and that is the error state.
        if (isFirst) setStatus('error')
      }
      refreshPolicies()
    }

    read(true)
    const interval = setInterval(() => read(false), DECISIONS_POLL_SECONDS * 1000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [customerId, attempt, refreshPolicies])

  // Single ticking clock expires anything past its deadline, locally only.
  // Never calls resolve here — an unanswered step_up ends paused, not
  // decided (docs/rules.md Q2: never invent a human answer). The
  // category stays 'uncertain' forever (D-041) — only uncertain_outcome moves.
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now()
      setDecisions((current) => {
        let changed = false
        const next = current.map((d) => {
          if (d.status !== 'pending_human' || !d.deadline_at) return d
          if (new Date(d.deadline_at).getTime() > now) return d
          changed = true
          return { ...d, uncertain_outcome: 'expired' as const, status: 'final' as const }
        })
        return changed ? next : current
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  // Never reclassifies the decision as 'approved'/'stopped' — a step_up's
  // human answer is a sub-status of Uncertain, not a new top-level category
  // (D-041): the customer's own words distinguish an automatic outcome from
  // one they answered themselves.
  async function resolve(authorizationId: string, answer: 'approve' | 'decline') {
    // Signed by this device, enrolled on the purchase's card (contract §3.10).
    const cardId = decisions.find((d) => d.authorization_id === authorizationId)?.card_id ?? ''
    await withDevice(cardId, () => resolveApproval(cardId, authorizationId, answer))
    setDecisions((current) =>
      current.map((d) =>
        d.authorization_id === authorizationId
          ? { ...d, uncertain_outcome: answer === 'approve' ? 'approved' : 'declined', status: 'final' }
          : d,
      ),
    )
    // An approved step-up is spend and leaves the pending reservation.
    refreshPolicies()
  }

  function retry() {
    setStatus('loading')
    setAttempt((n) => n + 1)
  }

  const pending = decisions.filter((d) => d.status === 'pending_human')

  return (
    <DecisionsContext.Provider value={{ decisions, pending, status, resolve, retry }}>
      {children}
    </DecisionsContext.Provider>
  )
}

export function DecisionsProvider({ children }: { children: ReactNode }) {
  const { signedInAs } = useCustomer()
  const customerId = signedInAs?.customer_id ?? null
  return (
    <DecisionsProviderInner key={customerId ?? 'signed-out'} customerId={customerId}>
      {children}
    </DecisionsProviderInner>
  )
}
