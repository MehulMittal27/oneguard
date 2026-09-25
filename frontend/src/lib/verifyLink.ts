/** What a verify link (the passport's QR code) asks to check (`../../docs/passport.md`). */
export type VerifyLinkRequest = { passport_id: string; version?: number } | { receipt_id: string }

/** `?passport=<id>&v=<n>` or `?receipt=<id>`; `null` when the link names neither. */
export function verifyRequestFrom(search: string): VerifyLinkRequest | null {
  const params = new URLSearchParams(search)
  const receipt = params.get('receipt')
  if (receipt) return { receipt_id: receipt }
  const passport = params.get('passport')
  if (!passport) return null
  const version = Number(params.get('v'))
  return Number.isInteger(version) && version > 0 ? { passport_id: passport, version } : { passport_id: passport }
}
