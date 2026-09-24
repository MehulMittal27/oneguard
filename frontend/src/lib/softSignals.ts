import type { SoftSignalsState } from '../api/types'

/**
 * The operator strip's one toggle for D5. Live runs and the offline replay start
 * with different defaults (`../docs/api-contract.md` §3.7: on for live when the
 * model loaded, off for the replay), so before anyone presses it the two can
 * disagree. The label then says which one runs rather than rounding to on or off.
 */
export function softSignalsLabel(state: SoftSignalsState | null): string {
  if (!state) return '…'
  if (state.live && state.replay) return 'on'
  if (!state.live && !state.replay) return 'off'
  return state.live ? 'live only' : 'replay only'
}

/** What pressing the toggle asks D5 for: on everywhere unless it already is. */
export function nextSoftSignals(state: SoftSignalsState | null): boolean {
  return !(state?.live && state.replay)
}
