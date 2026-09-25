import { signedFetch } from '../lib/deviceKey'

/**
 * Answers a step-up (C8). Only ever called by the customer's own
 * Approve/Reject tap — never automatically, and never a second time for
 * the same authorization (docs/rules.md M7). Reading pending
 * step-ups is `getDecisions` (C6) filtered to `status: 'pending_human'` —
 * there is no separate read endpoint (C7 was folded back into C6). Signed by
 * this device, enrolled on the purchase's card (contract §3.10): the receipt
 * records which device gave the answer.
 */
export async function resolveApproval(
  cardId: string,
  authorizationId: string,
  decision: 'approve' | 'decline',
): Promise<void> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return
  }

  const response = await signedFetch(cardId, 'POST', `/authorizations/${authorizationId}/resolve`, { decision })
  if (!response.ok) {
    throw new Error(`Failed to resolve approval (${response.status})`)
  }
}
