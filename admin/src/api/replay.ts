import { api, getOrNull } from './client'
import type { ReplayStatus } from './types'

/** D1: the offline replay's own status. `null` means no replay has run yet. */
export function getReplayStatus(): Promise<ReplayStatus | null> {
  return getOrNull<ReplayStatus>('/dev/replay')
}

/** D2: replay a scenario from the local data pack offline, `speed_ms` apart. */
export function restartReplay(body: {
  scenario_id: string
  card_id: string
  speed_ms?: number
}): Promise<ReplayStatus> {
  return api.post<ReplayStatus>('/dev/replay/restart', body)
}
