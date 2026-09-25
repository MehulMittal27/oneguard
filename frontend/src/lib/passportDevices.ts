import type { Device } from '../api/types'

/**
 * What the card's Passport section offers this browser (`../../docs/passport.md`
 * 'Devices'). The backend enrols a card's first device at once (trust on first
 * use: no device enrolled yet) and leaves every later one pending until an
 * enrolled device approves it.
 *
 * - `controller`: no device controls the card, so this browser can become its
 *   controller now ('Make this device the controller').
 * - `add`: another device controls the card; this browser can ask to join
 *   ('Add this device'), which waits for that device's approval.
 * - `waiting`: this browser already asked and waits for approval.
 * - `null`: this browser controls the card; nothing to offer.
 */
export type DeviceOffer = 'controller' | 'add' | 'waiting' | null

export function deviceOffer(devices: Device[], mine: Device | null): DeviceOffer {
  if (mine?.status === 'enrolled') return null
  if (mine?.status === 'pending') return 'waiting'
  return devices.some((d) => d.status === 'enrolled') ? 'add' : 'controller'
}
