import { createContext, useContext } from 'react'
import type { Customer } from '../api/types'

export interface CustomerContextValue {
  signedInAs: Customer | null
  // True while a `?customer=` deep link is being resolved (lib/deepLink.ts).
  signingIn: boolean
  continueAs: (customer: Customer) => void
  logout: () => void
}

export const CustomerContext = createContext<CustomerContextValue | null>(null)

export function useCustomer(): CustomerContextValue {
  const context = useContext(CustomerContext)
  if (!context) {
    throw new Error('useCustomer must be used within a CustomerProvider')
  }
  return context
}
