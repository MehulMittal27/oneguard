/**
 * The phone UI's two deep links (`../../docs/api-contract.md` §6 item 19), read
 * once at load, never written back:
 *
 * - `?customer=<customer_id>` signs in as that customer and skips the picker.
 *   Session only, like a sign-in by tapping: nothing is stored, a reload reads
 *   the URL again, and an id C12 does not list shows the picker.
 * - `?embed=1` draws the app without `DeviceFrame`'s bezel, for the operator
 *   console's embedded phone, which draws its own.
 */
export interface DeepLink {
  customerId: string | null
  embed: boolean
}

export function readDeepLink(search: string): DeepLink {
  const params = new URLSearchParams(search)
  const customerId = params.get('customer')?.trim() || null
  return { customerId, embed: params.get('embed') === '1' }
}

export const DEEP_LINK: DeepLink =
  typeof window === 'undefined' ? { customerId: null, embed: false } : readDeepLink(window.location.search)
