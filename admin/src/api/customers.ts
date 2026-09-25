import { api, getOrNull } from './client'
import type { Account, Customer, Mandate } from './types'

/** C12: every customer the store knows, and which of them have a live scenario. */
export async function listCustomers(): Promise<Customer[]> {
  const body = await api.get<{ customers: Customer[] }>('/customers')
  return body.customers
}

/** C10: a customer's accounts and cards. Read-only reference, never a decision input. */
export async function getAccounts(customerId: string): Promise<Account[]> {
  const body = await api.get<{ accounts: Account[] }>(`/customers/${encodeURIComponent(customerId)}/accounts`)
  return body.accounts
}

/** C3: the card's active mandate, or `null` if it has never had one. */
export async function getPolicy(cardId: string): Promise<Mandate | null> {
  const body = await getOrNull<{ mandate: Mandate | null }>(`/cards/${encodeURIComponent(cardId)}/policy`)
  return body?.mandate ?? null
}
