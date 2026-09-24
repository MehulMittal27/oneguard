const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

/**
 * Answers a step-up (C8). Only ever called by the customer's own
 * Approve/Reject tap — never automatically, and never a second time for
 * the same authorization (docs/rules.md M7). Reading pending
 * step-ups is `getDecisions` (C6) filtered to `status: 'pending_human'` —
 * there is no separate read endpoint (C7 was folded back into C6).
 */
export async function resolveApproval(
  authorizationId: string,
  decision: 'approve' | 'decline',
): Promise<void> {
  if (import.meta.env.VITE_USE_MOCKS === 'true') {
    return
  }

  const response = await fetch(`${API_BASE_URL}/authorizations/${authorizationId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
  if (!response.ok) {
    throw new Error(`Failed to resolve approval (${response.status})`)
  }
}
