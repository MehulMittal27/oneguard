const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * The card passport and signed receipts, read-only, for the operator console.
 *
 * These endpoints arrive with the passport work (`p1/passport`) and may be
 * absent from the backend this build talks to. Every call here therefore
 * resolves to `null` when there is no answer (a 404, any other refusal, or no
 * network) and never throws: the console then shows "Passport —" and carries on.
 * Shapes are read defensively in `lib/opsConsole.ts` for the same reason.
 *
 * Nothing here enrols, approves or removes a device: that stays on the phone UI.
 */

/** `GET /api/cards/{card_id}/passport`: the card's passport document, or null. */
export async function getPassport(cardId: string): Promise<unknown | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return null
  try {
    const response = await fetch(`${API_BASE_URL}/cards/${encodeURIComponent(cardId)}/passport`)
    if (!response.ok) return null
    return (await response.json()) as unknown
  } catch {
    return null
  }
}

/** `GET /api/cards/{card_id}/passport/qr.svg`: the passport as a QR code. */
export function passportQrUrl(cardId: string): string {
  return `${API_BASE_URL}/cards/${encodeURIComponent(cardId)}/passport/qr.svg`
}

/**
 * `POST /api/verify` with a signed document (a passport or a decision receipt)
 * as the body: the server checks the signature. Null when nobody answered.
 */
export async function verifySigned(document: unknown): Promise<unknown | null> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') return null
  try {
    const response = await fetch(`${API_BASE_URL}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(document),
    })
    if (response.status === 404 || response.status === 405) return null
    // A refusal still carries the verdict (a bad signature is a 4xx with a body).
    return (await response.json()) as unknown
  } catch {
    return null
  }
}
