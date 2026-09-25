import { createContext, useContext } from 'react'

/** The customer closed the enrolment sheet: the change they asked for was not sent. Not an error to show. */
export class DeviceGateCancelled extends Error {
  constructor() {
    super('Enrolment cancelled')
    this.name = 'DeviceGateCancelled'
  }
}

export interface DeviceContextValue {
  /**
   * Runs a device-bound change (`../../docs/api-contract.md` §3.10) for a card
   * once this browser may make it: enrolled silently when it is the card's
   * first device; otherwise the enrolment sheet opens ("This device isn't
   * approved for this card yet") and the change goes out once an enrolled
   * device has approved this one. Rejects with `DeviceGateCancelled` when the
   * customer closes the sheet — nothing was sent.
   */
  withDevice: <T>(cardId: string, action: () => Promise<T>) => Promise<T>
  // Opens the sheet to add this browser to the card (Passport section).
  requestEnrol: (cardId: string) => void
  // Bumped whenever this browser's devices change, so device lists re-read.
  version: number
}

export const DeviceContext = createContext<DeviceContextValue | null>(null)

export function useDevice(): DeviceContextValue {
  const context = useContext(DeviceContext)
  if (!context) {
    throw new Error('useDevice must be used within a DeviceProvider')
  }
  return context
}
