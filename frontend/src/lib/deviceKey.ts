import { canonical } from './canonical'

/**
 * This browser as a device that may control a card (`../../docs/passport.md`
 * §4, `../../docs/api-contract.md` §3.10).
 *
 * One ECDSA P-256 key pair, made by WebCrypto with `extractable: false` and
 * kept in IndexedDB (`oneguard-device`): the private key never leaves the
 * browser, not even to this code. The backend keeps only the public JWK. Each
 * card this browser enrols on hands back its own `device_id`, remembered here
 * per card. A change to what an agent may do (confirm, tighten, revoke, answer
 * a step-up, approve or remove a device) goes out through `signedFetch`.
 */

const DB_NAME = 'oneguard-device'
const STORE = 'keys'
const RECORD = 'self'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

export interface DeviceKey {
  privateKey: CryptoKey
  publicJwk: JsonWebKey
  // Default name for enrolment ("Chrome on macOS"); the customer may edit it
  // once, when enrolling.
  label: string
  // card id → the device id that card's enrolment returned.
  deviceIds: Record<string, string>
}

/** A device-bound call the backend refused because this browser is not (yet) enrolled on the card. */
export class DeviceNotEnrolledError extends Error {
  cardId: string
  constructor(cardId: string) {
    super(`This device isn't approved for card ${cardId} yet`)
    this.name = 'DeviceNotEnrolledError'
    this.cardId = cardId
  }
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function read(): Promise<DeviceKey | undefined> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(RECORD)
    request.onsuccess = () => resolve(request.result as DeviceKey | undefined)
    request.onerror = () => reject(request.error)
  })
}

async function write(key: DeviceKey): Promise<void> {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.objectStore(STORE).put(key, RECORD)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

/** "Chrome on macOS" — a first guess at a name the customer recognises. */
export function defaultDeviceLabel(userAgent: string = navigator.userAgent): string {
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /Firefox\//.test(userAgent)
      ? 'Firefox'
      : /Chrome\//.test(userAgent)
        ? 'Chrome'
        : /Safari\//.test(userAgent)
          ? 'Safari'
          : 'Browser'
  const os = /iPhone|iPad/.test(userAgent)
    ? 'iOS'
    : /Android/.test(userAgent)
      ? 'Android'
      : /Mac OS X/.test(userAgent)
        ? 'macOS'
        : /Windows/.test(userAgent)
          ? 'Windows'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : 'this device'
  return `${browser} on ${os}`
}

let pending: Promise<DeviceKey> | null = null

/** This browser's key, made on first use. Concurrent callers share one. */
export function getOrCreateDeviceKey(): Promise<DeviceKey> {
  pending ??= (async () => {
    const stored = await read()
    if (stored) return stored
    const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify'])
    // A public key is always exportable, whatever `extractable` says.
    const publicJwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
    const key: DeviceKey = {
      privateKey: pair.privateKey,
      publicJwk: { kty: publicJwk.kty, crv: publicJwk.crv, x: publicJwk.x, y: publicJwk.y },
      label: defaultDeviceLabel(),
      deviceIds: {},
    }
    await write(key)
    return key
  })().catch((error) => {
    pending = null
    throw error
  })
  return pending
}

/** Remember (or, with `null`, forget) this browser's device id on a card. */
export async function rememberDeviceId(cardId: string, deviceId: string | null): Promise<DeviceKey> {
  const key = await getOrCreateDeviceKey()
  const deviceIds = { ...key.deviceIds }
  if (deviceId) deviceIds[cardId] = deviceId
  else delete deviceIds[cardId]
  const next = { ...key, deviceIds }
  await write(next)
  pending = Promise.resolve(next)
  return next
}

function base64(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

function nonce(): string {
  return base64(crypto.getRandomValues(new Uint8Array(16))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/**
 * A device-bound request (`../../docs/api-contract.md` §3.10) for `cardId`,
 * signed with this browser's key: `X-OneGuard-Device`, `-Ts`, `-Nonce` and
 * `-Signature` over canonical `{ method, path, body, ts, nonce }`. Throws
 * `DeviceNotEnrolledError` when this browser has no device on the card, or the
 * backend answers `device_not_enrolled` (removed, or still waiting).
 */
export async function signedFetch(cardId: string, method: string, path: string, body?: unknown): Promise<Response> {
  const key = await getOrCreateDeviceKey()
  const deviceId = key.deviceIds[cardId]
  if (!deviceId) throw new DeviceNotEnrolledError(cardId)
  const url = `${API_BASE_URL}${path}`
  // What the server parses: undefined members dropped, as JSON.stringify drops them.
  const sent = body === undefined ? null : (JSON.parse(JSON.stringify(body)) as unknown)
  const ts = Math.floor(Date.now() / 1000)
  const once = nonce()
  const payload = canonical({ method: method.toUpperCase(), path: new URL(url, window.location.href).pathname, body: sent, ts, nonce: once })
  const signature = await crypto.subtle.sign(
    { name: 'ECDSA', hash: 'SHA-256' },
    key.privateKey,
    new TextEncoder().encode(payload),
  )
  const response = await fetch(url, {
    method,
    headers: {
      ...(sent === null ? {} : { 'Content-Type': 'application/json' }),
      'X-OneGuard-Device': deviceId,
      'X-OneGuard-Ts': String(ts),
      'X-OneGuard-Nonce': once,
      'X-OneGuard-Signature': base64(new Uint8Array(signature)),
    },
    body: sent === null ? undefined : JSON.stringify(sent),
  })
  if (response.status === 401) {
    const code = await response
      .clone()
      .json()
      .then((data: { error?: { code?: string } }) => data.error?.code)
      .catch(() => undefined)
    if (code === 'device_not_enrolled') throw new DeviceNotEnrolledError(cardId)
  }
  return response
}
