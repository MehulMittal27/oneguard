import { useState, type ReactNode } from 'react'
import type { Customer } from '../api/types'
import { CustomerContext } from './CustomerContext'

export function CustomerProvider({ children }: { children: ReactNode }) {
  const [signedInAs, setSignedInAs] = useState<Customer | null>(null)

  function logout() {
    // Client-side only (D-021): no API call, no mandate change. There's no
    // polling/SSE subscription yet to cancel — this is the hook point for
    // one once a later slice adds it.
    setSignedInAs(null)
  }

  return (
    <CustomerContext.Provider value={{ signedInAs, continueAs: setSignedInAs, logout }}>
      {children}
    </CustomerContext.Provider>
  )
}
