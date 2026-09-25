import { api, getOrNull } from './client'
import type { CurrentRun, LiveRun, ReplayStatus } from './types'

/** D3: start a Viseca run. `force: true` skips the "one run at a time" checks. */
export function createRun(body: { scenario_id: string; card_id: string; force?: boolean }): Promise<LiveRun> {
  return api.post<LiveRun>('/dev/runs', body)
}

/** D4: the worker's view of a live run (progress, counters, worker health). */
export function getRun(runId: string): Promise<LiveRun> {
  return api.get<LiveRun>(`/dev/runs/${encodeURIComponent(runId)}`)
}

/** D7: the newest run, live or replay, by real start time. `null` when none has started. */
export async function getCurrentRun(): Promise<CurrentRun | null> {
  const body = await getOrNull<LiveRun | ReplayStatus>('/dev/runs/current')
  if (body === null) return null
  return 'run_id' in body ? { kind: 'live', run: body } : { kind: 'replay', run: body }
}
