import { api } from './client'
import type { Scenario } from './types'

/** D8: every scenario in the store's catalogue, served or not, bound or not. */
export async function listScenarios(): Promise<Scenario[]> {
  const body = await api.get<{ scenarios: Scenario[] }>('/dev/scenarios')
  return body.scenarios
}
