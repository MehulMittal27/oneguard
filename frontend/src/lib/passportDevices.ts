import type { Device } from '../api/types'

/**
 * Who controls a card and what the Passport section offers this browser
 * (`../../docs/passport.md` "Devices"). The card's first device is its
 * controller (trust on first use); a later one waits `pending` until the
 * controller approves it, and is then an approved device. Every enrolled
 * device changes the policy and answers step-ups; only the controller approves,
 * removes and hands over control.
 */

/**
 * The card's controller: the device the backend names (`role`), else, from a
 * backend before controllers, the earliest enrolled device, as the backend
 * itself reads rows from before controllers. None while no device is enrolled.
 */
export function controllerOf(devices: Device[]): Device | null {
  const enrolled = devices.filter((d) => d.status === 'enrolled')
  const named = enrolled.find((d) => d.role === 'controller')
  if (named) return named
  if (enrolled.some((d) => d.role !== undefined)) return null // the backend names roles and none is the controller
  return [...enrolled].sort((a, b) => (a.enrolled_at ?? '').localeCompare(b.enrolled_at ?? ''))[0] ?? null
}

/** This browser manages the card's devices: it is the controller. */
export function isController(devices: Device[], mine: Device | null): boolean {
  const controller = controllerOf(devices)
  return mine !== null && controller !== null && mine.status === 'enrolled' && controller.device_id === mine.device_id
}

/** "Controller · this device", "Controller · Stage laptop", or null with no controller. */
export function controllerLine(devices: Device[], mine: Device | null): string | null {
  const controller = controllerOf(devices)
  if (!controller) return null
  return `Controller · ${controller.device_id === mine?.device_id ? 'this device' : controller.label}`
}

/** What a device waiting for approval is waiting for, naming the controller. */
export function waitingLine(devices: Device[]): string {
  const controller = controllerOf(devices)
  return controller ? `Waiting for approval from ${controller.label}` : 'Waiting for approval from the card’s controller'
}

/**
 * What the section offers this browser:
 * - `controller`: no device controls the card, so this browser can become its
 *   controller now ("Make this device the controller").
 * - `add`: another device controls the card; this browser can ask to join
 *   ("Add this device"), which waits for the controller's approval.
 * - `waiting`: this browser already asked and waits for approval.
 * - `null`: this browser is enrolled (controller or approved); nothing to offer.
 */
export type DeviceOffer = 'controller' | 'add' | 'waiting' | null

export function deviceOffer(devices: Device[], mine: Device | null): DeviceOffer {
  if (mine?.status === 'enrolled') return null
  if (mine?.status === 'pending') return 'waiting'
  return devices.some((d) => d.status === 'enrolled') ? 'add' : 'controller'
}
