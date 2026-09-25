import { useEffect, useState, type ReactNode } from 'react'
import { getCustomers } from '../api/customers'
import type { Customer } from '../api/types'
import { DEEP_LINK } from '../lib/deepLink'
import { CustomerContext } from './CustomerContext'

export function CustomerProvider({ children }: { children: ReactNode }) {
  const [signedInAs, setSignedInAs] = useState<Customer | null>(null)
  // `?customer=` (lib/deepLink.ts): until C12 has answered, nothing is drawn,
  // so the picker never flashes up before the signed-in app replaces it.
  const [signingIn, setSigningIn] = useState(DEEP_LINK.customerId !== null)

  useEffect(() => {
    const wanted = DEEP_LINK.customerId
    if (wanted === null) return
    let cancelled = false
    getCustomers()
      .then((customers) => {
        if (cancelled) return
        // An id C12 does not list signs nobody in: the picker shows instead.
        setSignedInAs(customers.find((c) => c.customer_id === wanted) ?? null)
      })
      .catch(() => {
        // The picker has its own error state and retry; it takes over.
      })
      .finally(() => {
        if (!cancelled) setSigningIn(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function logout() {
    // Client-side only (D-021): no API call, no mandate change. There's no
    // polling/SSE subscription yet to cancel — this is the hook point for
    // one once a later slice adds it.
    setSignedInAs(null)
  }

  return (
    <CustomerContext.Provider value={{ signedInAs, signingIn, continueAs: setSignedInAs, logout }}>
      {children}
    </CustomerContext.Provider>
  )
}
