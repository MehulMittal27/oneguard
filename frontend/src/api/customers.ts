import type { Customer } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * Lists the demo customers for the sign-in screen (contract capability C12 —
 * a UI-only construct, not a Viseca API concept). In mock mode this resolves
 * from a local fixture instead of a real request; swapping to the backend
 * once it exists is deleting the `if` branch, not touching call sites.
 */
export async function getCustomers(): Promise<Customer[]> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    const { customers } = await import('../mocks/fixtures/customers.json')
    return customers as Customer[]
  }

  const response = await fetch(`${API_BASE_URL}/customers`)
  if (!response.ok) {
    throw new Error(`Failed to load customers (${response.status})`)
  }
  const data = (await response.json()) as { customers: Customer[] }
  return data.customers
}
