import type { Device, Passport, Receipt, VerifyResult } from './types'
import { getOrCreateDeviceKey, rememberDeviceId, signedFetch } from '../lib/deviceKey'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL
const MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

/**
 * Passport, receipts, verification and devices (`../../docs/api-contract.md`
 * §1.3, `../../docs/passport.md`). Reads and verification are open; approving
 * or removing a device is signed by an enrolled one (`signedFetch`).
 *
 * Mock mode serves `mocks/fixtures/passport.json` (a passport for card CA0039,
 * two devices, one receipt) and keeps device changes for the page's lifetime.
 */

interface PassportFixture {
  passport: Passport
  devices: Device[]
  receipts: Record<string, Receipt>
}

let mockState: PassportFixture | null = null

async function fixture(): Promise<PassportFixture> {
  if (!mockState) {
    const loaded = (await import('../mocks/fixtures/passport.json')) as unknown as PassportFixture
    mockState = structuredClone({ passport: loaded.passport, devices: loaded.devices, receipts: loaded.receipts })
  }
  return mockState
}

async function json<T>(response: Response, what: string): Promise<T> {
  if (!response.ok) throw new Error(`Failed to ${what} (${response.status})`)
  return (await response.json()) as T
}

/** P2: the card's passport, latest version; `null` when the card has none yet. */
export async function getPassport(cardId: string): Promise<Passport | null> {
  if (MOCKS) {
    const { passport } = await fixture()
    return passport.document.card_id === cardId ? passport : null
  }
  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/passport`)
  if (response.status === 404) return null
  return json<Passport>(response, 'load the passport')
}

/** P3: where the QR code of the card's latest passport is served (an SVG). */
export function passportQrUrl(cardId: string, version: number): string {
  // The version keeps the browser from showing a cached code for an older one.
  return MOCKS ? '/mock-passport-qr.svg' : `${API_BASE_URL}/cards/${cardId}/passport/qr.svg?v=${version}`
}

/** P4: the decision's signed receipt; `null` when there is none. */
export async function getReceipt(authorizationId: string): Promise<Receipt | null> {
  if (MOCKS) return (await fixture()).receipts[authorizationId] ?? null
  const response = await fetch(`${API_BASE_URL}/authorizations/${authorizationId}/receipt`)
  if (response.status === 404) return null
  return json<Receipt>(response, 'load the receipt')
}

export type VerifyRequest =
  | { document: Record<string, unknown>; signature: string; key_id: string }
  | { passport_id: string; version?: number }
  | { receipt_id: string }

/** P5: checks a passport or receipt against OneGuard's public key. */
export async function verifyDocument(body: VerifyRequest): Promise<VerifyResult> {
  if (MOCKS) {
    const { passport, receipts } = await fixture()
    const receipt = Object.values(receipts)[0]
    const document = 'document' in body ? body.document : 'receipt_id' in body ? receipt.document : passport.document
    const kind = document.type === 'oneguard.receipt/1' ? 'receipt' : 'passport'
    return {
      valid: true,
      document_type: kind,
      key_id: passport.key_id,
      issued_at: String(kind === 'receipt' ? document.decided_at : document.issued_at),
      reason: `Signed by OneGuard key ${passport.key_id}.`,
      document,
      current: kind === 'passport' ? true : null,
    }
  }
  const response = await fetch(`${API_BASE_URL}/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return json<VerifyResult>(response, 'verify')
}

/** P6: every device on the card, enrolled first. */
export async function getDevices(cardId: string): Promise<Device[]> {
  if (MOCKS) return (await fixture()).devices.filter((d) => d.card_id === cardId)
  const data = await json<{ devices: Device[] }>(await fetch(`${API_BASE_URL}/cards/${cardId}/devices`), 'load devices')
  return data.devices
}

/**
 * P7: offers this browser's public key to the card. The card's first device is
 * enrolled at once; any other waits `pending` for an enrolled one to approve it.
 * The device id is remembered for signing. The same key again is the same device.
 */
export async function enrolThisDevice(cardId: string, label?: string): Promise<Device['status']> {
  const key = await getOrCreateDeviceKey()
  if (MOCKS) {
    await rememberDeviceId(cardId, 'mock-this-device')
    return 'enrolled'
  }
  const response = await fetch(`${API_BASE_URL}/cards/${cardId}/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ public_key_jwk: key.publicJwk, label: label?.trim() || key.label }),
  })
  const enrolled = await json<{ device_id: string; status: Device['status'] }>(response, 'add this device')
  await rememberDeviceId(cardId, enrolled.device_id)
  return enrolled.status
}

/**
 * This browser's device on the card as the backend lists it, if it has one. In
 * mock mode this browser is the fixture's enrolled device, so Approve and
 * Remove can be shown offline.
 */
export async function thisDevice(cardId: string, devices?: Device[]): Promise<Device | null> {
  const listed = devices ?? (await getDevices(cardId))
  if (MOCKS) return listed.find((d) => d.status === 'enrolled') ?? null
  const deviceId = (await getOrCreateDeviceKey()).deviceIds[cardId]
  return deviceId ? (listed.find((d) => d.device_id === deviceId) ?? null) : null
}

type DeviceAction = 'approve' | 'remove' | 'transfer'

async function changeDevice(cardId: string, deviceId: string, action: DeviceAction): Promise<Device> {
  if (MOCKS) {
    const state = await fixture()
    const now = new Date().toISOString()
    state.devices = state.devices.map((d): Device => {
      if (action === 'transfer') {
        if (d.status !== 'enrolled') return d
        return { ...d, role: d.device_id === deviceId ? 'controller' : 'approved' }
      }
      if (d.device_id !== deviceId) return d
      return action === 'approve'
        ? { ...d, status: 'enrolled', role: 'approved', enrolled_at: now, enrolled_by_device_id: 'mock-this-device' }
        : { ...d, status: 'removed', role: null, removed_at: now }
    })
    return state.devices.find((d) => d.device_id === deviceId) as Device
  }
  const response = await signedFetch(cardId, 'POST', `/cards/${cardId}/devices/${deviceId}/${action}`)
  // 409 (its state) and 403 not_controller (only the card's controller manages
  // devices): the server's own words, shown under the list.
  if (response.status === 409 || response.status === 403) {
    const data = (await response.json()) as { error?: { message?: string } }
    throw new Error(data.error?.message ?? `Couldn't ${action} this device`)
  }
  return json<Device>(response, `${action} the device`)
}

/** P8: the controller (this device) lets a pending one sign for the card. */
export function approveDevice(cardId: string, deviceId: string): Promise<Device> {
  return changeDevice(cardId, deviceId, 'approve')
}

/** P9: the controller (this device) removes another; it cannot remove itself. */
export function removeDevice(cardId: string, deviceId: string): Promise<Device> {
  return changeDevice(cardId, deviceId, 'remove')
}

/** P10: the controller (this device) makes another enrolled device the controller. */
export function transferControl(cardId: string, deviceId: string): Promise<Device> {
  return changeDevice(cardId, deviceId, 'transfer')
}
