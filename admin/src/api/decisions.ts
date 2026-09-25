import { api } from './client'
import type { Decision } from './types'

/** C6: a customer's full decision history, newest first, across every card and run. */
export async function getDecisions(customerId: string): Promise<Decision[]> {
  const body = await api.get<{ decisions: Decision[] }>(`/customers/${encodeURIComponent(customerId)}/decisions`)
  return body.decisions
}
