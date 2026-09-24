import type { Account } from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * Lists a customer's accounts and cards for the Accounts screen (contract
 * capability C10, endpoint proposed — not yet backend-ratified). In mock
 * mode this resolves from a local fixture instead of a real request;
 * swapping to the backend once it exists is deleting the `if` branch, not
 * touching call sites.
 */
export async function getAccounts(customerId: string): Promise<Account[]> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    const { accounts } = await import('../mocks/fixtures/accounts.json')
    return (accounts as Account[]).filter((a) => a.customer_id === customerId)
  }

  const response = await fetch(`${API_BASE_URL}/customers/${customerId}/accounts`)
  if (!response.ok) {
    throw new Error(`Failed to load accounts (${response.status})`)
  }
  const data = (await response.json()) as { accounts: Account[] }
  return data.accounts
}
