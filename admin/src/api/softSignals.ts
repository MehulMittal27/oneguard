import { api } from './client'
import type { SoftSignalsState } from './types'

/** D5 read: whether the models run now, for live runs and the offline replay. */
export function getSoftSignals(): Promise<SoftSignalsState> {
  return api.get<SoftSignalsState>('/dev/soft-signals')
}

/** D5 write: the chaos toggle — switches live and replay together. */
export async function setSoftSignals(enabled: boolean): Promise<boolean> {
  const body = await api.post<{ enabled: boolean }>('/dev/soft-signals', { enabled })
  return body.enabled
}
